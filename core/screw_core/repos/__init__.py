"""
Репозитории для работы с БД.

Содержат только DB-логику, без бизнес-правил и aiogram.
"""

from core.screw_core.repos.attendance_repo import AttendanceRepo
from core.screw_core.repos.day_status_repo import DayStatusRepo
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.repos.feature_flags_repo import FeatureFlagsRepo
from core.screw_core.repos.flud_repo import FludRepo
from core.screw_core.repos.polls_repo import PollsRepo
from core.screw_core.repos.registration_repo import RegistrationRepo
from core.screw_core.repos.users_repo import UsersRepo

__all__ = [
    "UsersRepo",
    "RegistrationRepo",
    "PollsRepo",
    "AttendanceRepo",
    "DayStatusRepo",
    "EventsRepo",
    "FeatureFlagsRepo",
    "FludRepo",
]
