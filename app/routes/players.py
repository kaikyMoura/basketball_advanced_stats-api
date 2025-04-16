from collections import defaultdict
from typing import List, Literal, Optional

from fastapi.responses import JSONResponse
import pandas as pd
from app.services.nba_api.nba_client import (
    get_active_players,
    get_all_players,
    get_player_awards,
    get_player_carrer_totals,
    get_player_seasons_dashboard,
    get_player_dashboard_by_year_over_year,
    get_player_info,
    get_inactive_players,
)
from fastapi import APIRouter, FastAPI, HTTPException, Query

from app.utils.clean_json import clean_nan

app = FastAPI()
router = APIRouter()


@router.get("/players", response_model=List[dict])
def get_players(
    is_active: Optional[bool] = Query(None, description="Filter by active players"),
    player_name: Optional[str] = Query(None, description="Filter by player name"),
    limit: Optional[int] = Query(None, description="Limit the number of players"),
    page: Optional[int] = Query(None, description="Paginate the teams"),
    pageSize: Optional[int] = Query(10, description="Paginate the teams"),
):

    players = []

    if is_active is True:
        players = get_active_players()
    elif is_active is False:
        players = get_inactive_players()
    else:
        players = get_all_players()

    if player_name:
        players = list(
            filter(lambda p: player_name.lower() in p["full_name"].lower(), players)
        )

    if limit:
        players = players[:limit]

    if page:
        page = page or 1
        players = players[(page - 1) * pageSize : page * pageSize]

    return JSONResponse(content=players)


@router.get("/players/stats/career/{player_id}", response_model=dict)
def get_player_career_stats(
    player_id: str,
    season_type: Optional[Literal["Regular Season", "Pre Season", "Playoffs"]] = Query(
        None, description="Filter by season type"
    ),
    season: Optional[str] = Query(
        None,
        description="Filter by specific season, e.g., '2023-24', All or empty to get the carrer totals",
    ),
    page: Optional[int] = Query(None, description="Paginate the seasons"),
    page_size: Optional[int] = Query(10, description="Paginate the seasons"),
):
    """
    Retrieve the career statistics for a specific player using provided parameters.

    Args:
        player_id (str): The unique identifier for the player.
        season_type (Literal["Regular Season", "Pre Season", "Playoffs"]): Filter by season type.
        season (str): Filter by specific season, e.g., '2023-24', All or empty to get the carrer totals.
        page (int): Paginate the seasons.
        page_size (int): Paginate the seasons.

    Returns:
        dict: A dictionary containing the career statistics information.

    Raises:
        HTTPException: If no career statistics are found for the player.
    """
    if not player_id:
        raise HTTPException(
            status_code=400,
            detail="Param player_id is required",
        )

    player_totals = get_player_carrer_totals(player_id)

    if season != "All":
        if season_type == "Playoffs":
            df = player_totals.career_totals_post_season.get_data_frame()
        else:
            df = player_totals.career_totals_regular_season.get_data_frame()
    else:
        if season_type == "Playoffs":
            df = player_totals.season_totals_post_season.get_data_frame()
        else:
            df = player_totals.season_totals_regular_season.get_data_frame()

    df.columns = df.columns.str.lower()

    if season and season != "All":
        df = df[df["season_id"] == season]
        if df.empty:
            raise HTTPException(
                status_code=404,
                detail=f"No season stats found for this player in season {season}",
            )

    if "gp" not in df.columns or df["gp"].sum() == 0:
        return JSONResponse(
            content={
                "season_type": season_type or "Regular Season",
                "totals" if season != "All" else "seasons": [],
            }
        )

    stats_columns = [
        "pts",
        "reb",
        "ast",
        "stl",
        "blk",
        "tov",
        "fgm",
        "fga",
        "fg3m",
        "fg3a",
        "ftm",
        "fta",
    ]

    for stat in stats_columns:
        if stat in df.columns:
            df[f"{stat}_per_game"] = (df[stat] / df["gp"]).round(1)

    if page:
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        df = df.iloc[start_idx:end_idx]

    response_key = "totals" if season != "All" else "seasons"

    records = df.drop(columns=["player_id"]).to_dict(orient="records")

    safe_data = clean_nan(records)
    return JSONResponse(
        content={
            "season_type": season_type or "Regular Season",
            response_key: safe_data,
        }
    )


# Retrieve general information about the player (age, height, weight, etc.)
@router.get("/players/player/info", response_model=dict)
def get_player_common_info(
    player_id: Optional[int] = Query(None, description="Filter by player id"),
    player_name: Optional[str] = Query(None, description="Filter by player name"),
):

    if not player_id and not player_name:
        raise HTTPException(
            status_code=400, detail="Either player_id or player_name must be provided"
        )

    if player_id:
        player_info = get_player_info(player_id)
        return {"player_id": player_id, "player_info": player_info}

    if player_name:
        all_players = get_all_players()
        filtered_players = list(
            filter(lambda p: player_name.lower() in p["full_name"].lower(), all_players)
        )

        if not filtered_players:
            raise HTTPException(
                status_code=404, detail="No players found with that name"
            )

        player_infos = [get_player_info(player["id"]) for player in filtered_players]

        df = pd.DataFrame(player_infos)
        df.columns = df.columns.str.lower()

        return JSONResponse(content=df.to_dict(orient="records"))


@router.get("/players/player/awards", response_model=dict)
def fetch_player_awards(
    player_id: int = Query(None, description="Filter by player id"),
    detailed: Optional[bool] = Query(
        False, description="Return detailed awards information"
    ),
):
    """
    Fetches awards for a specific player by their player ID.

    Args:
        player_id (int): The unique identifier for the player.
        detailed (bool, optional): If True, returns detailed awards information including descriptions. Defaults to False.

    Returns:
        dict: A dictionary containing either a summary string of awards or detailed awards information.
            - If detailed is False, returns a summary string of awards.
            - If detailed is True, returns a dictionary with 'summary' and 'details' keys.

    Raises:
        HTTPException: If player_id is not provided or if no awards are found for the player.
    """

    if not player_id:
        raise HTTPException(
            status_code=400, detail="Missing required parameter: player_id"
        )

    raw_awards = get_player_awards(player_id)

    if not raw_awards:
        return {"summary": "", "details": []} if detailed else ""

    award_counts = defaultdict(int)

    processed_awards = []

    for award in raw_awards:
        description = award.get("description", "")

        if "All-Defensive" in description:
            award_type = "All-Defensive Team"
        elif "All-Star" in description:
            award_type = "NBA All-Star"
        elif "Player of the Week" in description:
            award_type = "NBA Player of the Week"
        elif "Gold Medal" in description:
            award_type = "Olympic Gold Medal"
        else:
            award_type = description

        award_counts[award_type] += 1

        processed_awards.append(award)

    summary = " | ".join(
        [
            f"{count} {award}"
            for award, count in sorted(
                award_counts.items(), key=lambda x: (-x[1], x[0])
            )
        ]
    )

    if not detailed:
        return JSONResponse(content=summary)

    return {"summary": summary, "details": raw_awards}


@router.get("/players/stats/advanced/{player_id}", response_model=dict)
def get_player_advanced_stats(
    player_id: int,
    per_mode: Literal[
        "Totals",
        "PerGame",
        "MinutesPer",
        "Per48",
        "Per40",
        "Per36",
        "PerMinute",
        "PerPossession",
        "PerPlay",
        "Per100Possessions",
        "Per100Plays",
    ] = "Totals",
    season: Optional[str] = Query(
        None, description="Filter by season: (2022-23) or All"
    ),
    season_type: Literal["Regular Season", "Pre Season", "Playoffs"] = "Regular Season",
):
    """
    Retrieve advanced statistics for a specific player using provided parameters.

    Args:
        player_id (int): The unique identifier for the player.
        per_mode (Literal["Totals", "PerGame", "MinutesPer", "Per48", "Per40", "Per36", "PerMinute", "PerPossession", "PerPlay", "Per100Possessions", "Per100Plays"]): The type of advanced statistics to retrieve.
        season (Optional[str]): Filter by season: (2022-23) or All
        season_type (Literal["Regular Season", "Pre Season", "Playoffs"]): Filter by season type

    Returns:
        dict: A dictionary containing the advanced statistics information.

    Raises:
        HTTPException: If no advanced statistics are found for the player.
    """
    params = {
        "player_id": player_id,
        "season_type_playoffs": season_type,
    }

    if per_mode:
        params["per_mode_detailed"] = per_mode
    if season:
        params["season"] = season

    if season == "All":
        fantasy_profile_df = get_player_seasons_dashboard(params, 1)
        fantasy_profile_df.columns = fantasy_profile_df.columns.str.lower()
    else:
        fantasy_profile_df = get_player_dashboard_by_year_over_year(
            params
        ).get_data_frames()

    df = fantasy_profile_df

    if not df or "gp" not in df.columns or df["gp"].sum() == 0:
        return JSONResponse(
            content={
                "player_id": player_id,
                "per_mode": per_mode or "All",
                "season": season or "All",
                "season_type": season_type or "All",
                "stats": [],
            }
        )

    return JSONResponse(
        content={
            "player_id": player_id,
            "per_mode": per_mode or "All",
            "season": season or "All",
            "season_type": season_type or "All",
            "stats": df.drop(
                columns=[
                    col
                    for col in df.columns
                    if "_rank" in col
                    or "group_set" in col
                    or "wnba_fantasy_pts" in col
                    or "season" in col
                ],
                errors="ignore",
            ).to_dict(orient="records"),
        }
    )
