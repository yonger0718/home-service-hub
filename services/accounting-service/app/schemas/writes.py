"""Request bodies for write endpoints. Task 5 adds PreferenceIn; later tasks add the entry and settings inputs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_income_colors: Literal["red_green", "green_red"]
    keypad_layout: Literal["calculator", "phone"]
    week_start: int = Field(ge=0, le=6)
    main_currency: str = Field(pattern=r"^[A-Z0-9]{3,8}$")
    hide_rewards_on_timeline: bool
    abbreviate_totals: bool
