"""
Отправка результатов опроса на email после закрытия.

Формирует XLSX-файл с результатами и отправляет через EwsMailer.

XLSX-формат:
  ФИО | Статус | Отдел
  ---
  Отметившиеся: Эволюция / ЦСКА / Удалёнка
  Неотметившиеся: Неизвестно / Отпуск / Больничный / Отгул

Настройки:
- POLL_RESULTS_EMAIL_ENABLED: 0/1 (по умолчанию 0)
- POLL_RESULTS_EMAIL_TO: получатели через запятую (обязательно)
- POLL_RESULTS_EMAIL_CC: копия через запятую (опционально)
- POLL_RESULTS_EMAIL_SUBJECT: шаблон темы (опционально)
"""

import io
import os
from datetime import date

from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)

# Маппинг choice → читаемое название
CHOICE_LABELS = {
    "EVOLUTION": "Эволюция",
    "CSKA": "ЦСКА",
    "REMOTE": "Удалёнка",
}

# Маппинг day_status kind → читаемое название
DAY_STATUS_LABELS = {
    "VACATION": "Отпуск",
    "SICK": "Больничный",
    "LEAVE": "Отгул",
    "DAY_OFF": "Выходной",
}

DEPARTMENT_LABELS = {
    "OZKO": "ОЗКО",
    "OSB": "ОСБ",
}


# =============================================================================
# Конфигурация
# =============================================================================


def is_poll_results_email_enabled() -> bool:
    """Включена ли отправка результатов опроса по email."""
    return os.getenv("POLL_RESULTS_EMAIL_ENABLED", "0") == "1"


def _get_recipients_to() -> list[str]:
    """Получить список получателей TO."""
    value = os.getenv("POLL_RESULTS_EMAIL_TO", "")
    if not value.strip():
        return []
    return [e.strip() for e in value.split(",") if e.strip()]


def _get_recipients_cc() -> list[str]:
    """Получить список получателей CC."""
    value = os.getenv("POLL_RESULTS_EMAIL_CC", "")
    if not value.strip():
        return []
    return [e.strip() for e in value.split(",") if e.strip()]


def _get_subject_template() -> str:
    """Получить шаблон темы письма."""
    return os.getenv(
        "POLL_RESULTS_EMAIL_SUBJECT",
        "SCREW — Результаты опроса за {date}",
    )


def is_poll_results_email_configured() -> bool:
    """Проверить, полностью ли настроена отправка."""
    if not is_poll_results_email_enabled():
        return False

    from core.screw_core.services.ews_mailer import is_ews_configured
    if not is_ews_configured():
        return False

    if not _get_recipients_to():
        return False

    return True


# =============================================================================
# Формирование XLSX
# =============================================================================


async def _build_poll_results_xlsx(check_date: date) -> tuple[str, bytes]:
    """
    Сформировать XLSX с результатами опроса за дату.

    Returns:
        (filename, xlsx_bytes).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    from core.screw_core.repos.attendance_repo import AttendanceRepo
    from core.screw_core.repos.day_status_repo import DayStatusRepo
    from core.screw_core.repos.users_repo import UsersRepo

    # Данные
    marks = await AttendanceRepo.get_marks_for_date(check_date)
    all_users = await UsersRepo.get_users_with_departments()

    marked_user_ids: set[int] = set()
    for mark in marks:
        uid = mark.get("user_id")
        if uid:
            marked_user_ids.add(uid)

    users_by_id: dict[int, dict] = {u["id"]: u for u in all_users}

    # Workbook
    wb = Workbook()
    ws = wb.active
    date_str = check_date.strftime("%d.%m.%Y")
    ws.title = f"Опрос {date_str}"

    # Стили
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font_white = Font(bold=True, size=11, color="FFFFFF")
    header_alignment = Alignment(horizontal="center", vertical="center")
    absent_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

    # Заголовок документа
    ws.merge_cells("A1:C1")
    title_cell = ws["A1"]
    title_cell.value = f"Результаты опроса за {date_str}"
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal="center")

    # Заголовки таблицы
    row = 3
    headers = ["ФИО", "Статус", "Отдел"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=header)
        cell.font = header_font_white
        cell.fill = header_fill
        cell.alignment = header_alignment

    # Данные — отметившиеся
    row = 4
    for mark in marks:
        uid = mark.get("user_id")
        full_name = mark.get("full_name") or "Неизвестный"
        choice = mark.get("choice", "")
        status_label = CHOICE_LABELS.get(choice, choice)

        dept_label = ""
        if uid and uid in users_by_id:
            dept_label = DEPARTMENT_LABELS.get(users_by_id[uid].get("department_code", ""), "")

        ws.cell(row=row, column=1, value=full_name)
        ws.cell(row=row, column=2, value=status_label)
        ws.cell(row=row, column=3, value=dept_label)
        row += 1

    # Данные — неотметившиеся
    for u in all_users:
        uid = u["id"]
        if uid in marked_user_ids:
            continue

        full_name = u.get("full_name") or "Неизвестный"
        dept_label = DEPARTMENT_LABELS.get(u.get("department_code", ""), "")

        day_status = await DayStatusRepo.get_status(uid, check_date)
        if day_status:
            kind = day_status.get("kind", "")
            status_label = DAY_STATUS_LABELS.get(kind, "Неизвестно")
        else:
            status_label = "Неизвестно"

        cell_name = ws.cell(row=row, column=1, value=full_name)
        cell_status = ws.cell(row=row, column=2, value=status_label)
        cell_dept = ws.cell(row=row, column=3, value=dept_label)

        cell_name.fill = absent_fill
        cell_status.fill = absent_fill
        cell_dept.fill = absent_fill
        row += 1

    # Ширина колонок
    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 12

    # Сохраняем
    output = io.BytesIO()
    wb.save(output)
    xlsx_bytes = output.getvalue()
    output.close()

    filename = f"poll_results_{check_date.isoformat()}.xlsx"
    return filename, xlsx_bytes


# =============================================================================
# Отправка
# =============================================================================


async def send_poll_results_email(check_date: date) -> bool:
    """
    Отправить результаты опроса за дату по email (XLSX-вложение).

    Args:
        check_date: Дата опроса.

    Returns:
        True если отправлено успешно.
    """
    if not is_poll_results_email_configured():
        logger.debug("poll_results_email: не настроено, пропускаем")
        return False

    to_list = _get_recipients_to()
    cc_list = _get_recipients_cc()
    date_str = check_date.strftime("%d.%m.%Y")
    subject = _get_subject_template().format(date=date_str)

    try:
        filename, xlsx_bytes = await _build_poll_results_xlsx(check_date)
        logger.info(
            "poll_results_email: XLSX сформирован — %s, %d байт",
            filename,
            len(xlsx_bytes),
        )
    except Exception as e:
        logger.error("poll_results_email: ошибка формирования XLSX — %s", e)
        return False

    try:
        from core.screw_core.services.ews_mailer import Attachment, EwsMailer

        mailer = EwsMailer()
        mailer.send(
            to=to_list,
            subject=subject,
            body_text=f"Результаты опроса посещаемости за {date_str}.\n\nФайл с результатами во вложении.",
            cc=cc_list if cc_list else None,
            attachments=[
                Attachment(
                    filename=filename,
                    content=xlsx_bytes,
                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ],
        )
        mailer.close()

        logger.info("poll_results_email: успешно отправлено за %s", check_date)
        return True

    except Exception as e:
        logger.error("poll_results_email: ошибка отправки — %s", e)
        return False
