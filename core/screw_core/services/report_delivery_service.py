"""
Сервис доставки отчётов по email.

Отвечает за:
- Генерацию XLSX из данных отчёта
- Отправку через EWS
- Запись событий в БД (success/failure)

ВАЖНО: email-отчёты отправляются ТОЛЬКО руководству.
"""

import os
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Any

from core.screw_core.models.enums import EventType, NotifyChannel
from core.screw_core.repos.events_repo import EventsRepo
from core.screw_core.services.ews_mailer import (
    Attachment,
    EwsMailer,
    EwsMailerError,
    is_ews_configured,
)
from core.screw_core.services.reports import (
    DailyReportResult,
    MonthlyReportResult,
    ReportsService,
)
from core.screw_core.utils.logging import get_logger

logger = get_logger(__name__)


# MIME type для XLSX
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class DeliveryResult:
    """Результат доставки отчёта."""

    success: bool
    report_date: date
    recipients_count: int = 0
    error_summary: str | None = None
    event_id: int | None = None
    manual_retry: bool = False


@dataclass
class MonthlyDeliveryResult:
    """Результат доставки месячного отчёта."""

    success: bool
    year: int
    month: int
    recipients_count: int = 0
    error_summary: str | None = None
    event_id: int | None = None
    manual_retry: bool = False


def _get_report_to() -> list[str]:
    """Получить список получателей TO."""
    value = os.getenv("REPORT_TO", "")
    if not value.strip():
        return []
    return [email.strip() for email in value.split(",") if email.strip()]


def _get_report_cc() -> list[str]:
    """Получить список получателей CC."""
    value = os.getenv("REPORT_CC", "")
    if not value.strip():
        return []
    return [email.strip() for email in value.split(",") if email.strip()]


def build_daily_report_xlsx(report: DailyReportResult) -> bytes:
    """
    Сформировать XLSX из ежедневного отчёта.

    Args:
        report: Результат формирования отчёта.

    Returns:
        Байты XLSX файла.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        raise ImportError("openpyxl не установлен. Установите: pip install openpyxl")

    wb = Workbook()
    ws = wb.active
    ws.title = "Ежедневный отчёт"

    # Стили
    header_font = Font(bold=True)
    header_fill = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")

    # Заголовок
    date_str = report.report_date.strftime("%d.%m.%Y")
    ws["A1"] = f"Ежедневный отчёт за {date_str} (ОЗКО)"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:D1")

    # Раздел: Фактические отметки
    row = 3
    ws.cell(row=row, column=1, value="Фактические отметки").font = header_font
    row += 1
    ws.cell(row=row, column=1, value="Офис")
    ws.cell(row=row, column=2, value=report.actual_office)
    row += 1
    ws.cell(row=row, column=1, value="Удалёнка")
    ws.cell(row=row, column=2, value=report.actual_remote)
    row += 1
    ws.cell(row=row, column=1, value="Всего")
    ws.cell(row=row, column=2, value=report.total_marks)

    # Раздел: Участие в опросе
    row += 2
    ws.cell(row=row, column=1, value="Участие в опросе").font = header_font
    row += 1
    ws.cell(row=row, column=1, value="Процент")
    ws.cell(row=row, column=2, value=f"{report.poll_participation_percent}%")
    ws.cell(row=row, column=3, value=f"({report.total_marks}/{report.total_active_users})")

    # Раздел: Нарушения
    if report.violations_by_type:
        row += 2
        ws.cell(row=row, column=1, value="Нарушения").font = header_font
        row += 1

        violation_labels = {
            "NO_CHECKIN": "Не отметились в опросе",
        }

        for event_type, count in report.violations_by_type.items():
            label = violation_labels.get(event_type, event_type)
            ws.cell(row=row, column=1, value=label)
            ws.cell(row=row, column=2, value=count)
            row += 1

        ws.cell(row=row, column=1, value="Всего нарушений").font = header_font
        ws.cell(row=row, column=2, value=report.total_violations)

    # Раздел: Топ нарушителей
    if report.top_violators:
        row += 2
        ws.cell(row=row, column=1, value="Топ нарушителей").font = header_font
        row += 1

        # Заголовки таблицы
        ws.cell(row=row, column=1, value="№").font = header_font
        ws.cell(row=row, column=1).fill = header_fill
        ws.cell(row=row, column=2, value="ФИО").font = header_font
        ws.cell(row=row, column=2).fill = header_fill
        ws.cell(row=row, column=3, value="Нарушений").font = header_font
        ws.cell(row=row, column=3).fill = header_fill
        row += 1

        for i, violator in enumerate(report.top_violators, 1):
            ws.cell(row=row, column=1, value=i)
            ws.cell(row=row, column=2, value=violator.get("full_name", "—"))
            ws.cell(row=row, column=3, value=violator.get("violation_count", 0))
            row += 1

    # Автоширина колонок
    for col_idx in range(1, 5):
        ws.column_dimensions[chr(64 + col_idx)].width = 30

    # Сохраняем в bytes
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.read()


def build_monthly_report_xlsx(report: MonthlyReportResult) -> bytes:
    """
    Сформировать XLSX из месячного отчёта.

    Содержит лист "Сводка месяца" с ключевыми метриками.

    Args:
        report: Результат формирования месячного отчёта.

    Returns:
        Байты XLSX файла.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        raise ImportError("openpyxl не установлен. Установите: pip install openpyxl")

    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка месяца"

    # Стили
    header_font = Font(bold=True)
    header_fill = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")

    # Названия месяцев
    month_names = [
        "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
    ]
    month_name = month_names[report.month] if 1 <= report.month <= 12 else str(report.month)

    # Заголовок
    ws["A1"] = f"Сводка месяца: {month_name} {report.year} (ОЗКО)"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:C1")

    row = 3

    # Раздел: Общая информация
    ws.cell(row=row, column=1, value="Общая информация").font = header_font
    ws.cell(row=row, column=1).fill = header_fill
    ws.cell(row=row, column=2).fill = header_fill
    row += 1
    ws.cell(row=row, column=1, value="Рабочих дней в месяце")
    ws.cell(row=row, column=2, value=report.working_days_count)
    row += 1
    ws.cell(row=row, column=1, value="Активных пользователей")
    ws.cell(row=row, column=2, value=report.total_active_users)

    # Раздел: Офисные дни
    row += 2
    ws.cell(row=row, column=1, value="Офисные дни").font = header_font
    ws.cell(row=row, column=1).fill = header_fill
    ws.cell(row=row, column=2).fill = header_fill
    row += 1
    ws.cell(row=row, column=1, value="Всего")
    ws.cell(row=row, column=2, value=report.office_days_total)
    row += 1
    ws.cell(row=row, column=1, value="Эволюция")
    ws.cell(row=row, column=2, value=report.office_days_evolution)
    row += 1
    ws.cell(row=row, column=1, value="ЦСКА")
    ws.cell(row=row, column=2, value=report.office_days_cska)

    # Раздел: Удалённые дни
    row += 2
    ws.cell(row=row, column=1, value="Удалённые дни").font = header_font
    ws.cell(row=row, column=1).fill = header_fill
    ws.cell(row=row, column=2).fill = header_fill
    row += 1
    ws.cell(row=row, column=1, value="Всего")
    ws.cell(row=row, column=2, value=report.remote_days)

    # Раздел: Статусы
    row += 2
    ws.cell(row=row, column=1, value="Статусы").font = header_font
    ws.cell(row=row, column=1).fill = header_fill
    ws.cell(row=row, column=2).fill = header_fill
    row += 1
    ws.cell(row=row, column=1, value="Отпуска")
    ws.cell(row=row, column=2, value=report.vacation_days)
    row += 1
    ws.cell(row=row, column=1, value="Больничные")
    ws.cell(row=row, column=2, value=report.sick_days)
    row += 1
    ws.cell(row=row, column=1, value="Отгулы/выходные")
    ws.cell(row=row, column=2, value=report.leave_days)

    # Раздел: Нарушения
    row += 2
    ws.cell(row=row, column=1, value="Нарушения").font = header_font
    ws.cell(row=row, column=1).fill = header_fill
    ws.cell(row=row, column=2).fill = header_fill
    row += 1
    ws.cell(row=row, column=1, value="Не отметились")
    ws.cell(row=row, column=2, value=report.no_checkin_count)

    # Автоширина колонок
    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 15
    ws.column_dimensions["C"].width = 15

    # Сохраняем в bytes
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.read()


class ReportDeliveryService:
    """Сервис доставки отчётов по email."""

    @staticmethod
    async def send_daily_report(
        report_date: date,
        manual_retry: bool = False,
    ) -> DeliveryResult:
        """
        Отправить ежедневный отчёт по email.

        Args:
            report_date: Дата отчёта.
            manual_retry: True если это ручная повторная отправка.

        Returns:
            DeliveryResult с результатом доставки.
        """
        to_list = _get_report_to()
        cc_list = _get_report_cc()
        recipients_count = len(to_list) + len(cc_list)

        if not to_list:
            error_msg = "REPORT_TO не задан"
            logger.warning("report_delivery: %s", error_msg)
            return DeliveryResult(
                success=False,
                report_date=report_date,
                recipients_count=0,
                error_summary=error_msg,
                manual_retry=manual_retry,
            )

        if not is_ews_configured():
            error_msg = "EWS не настроен"
            logger.warning("report_delivery: %s", error_msg)
            return DeliveryResult(
                success=False,
                report_date=report_date,
                recipients_count=recipients_count,
                error_summary=error_msg,
                manual_retry=manual_retry,
            )

        try:
            # 1. Формируем отчёт
            report = await ReportsService.build_daily_report_ozko(report_date)

            # 2. Генерируем XLSX
            xlsx_bytes = build_daily_report_xlsx(report)

            # 3. Формируем тему и тело
            date_str = report_date.strftime("%d.%m.%Y")
            subject = f"Ежедневный отчёт ОЗКО за {date_str}"
            body_text = report.text  # Используем текстовую версию отчёта

            # 4. Создаём вложение
            filename = f"report_ozko_{report_date.isoformat()}.xlsx"
            attachment = Attachment(
                filename=filename,
                content=xlsx_bytes,
                mime_type=XLSX_MIME_TYPE,
            )

            # 5. Отправляем
            mailer = EwsMailer()
            try:
                mailer.send(
                    to=to_list,
                    cc=cc_list,
                    subject=subject,
                    body_text=body_text,
                    attachments=[attachment],
                )
            finally:
                mailer.close()

            # 6. Записываем событие успеха
            event_id = await ReportDeliveryService._record_event(
                event_type=EventType.REPORT_DAILY_SENT,
                report_date=report_date,
                recipients_count=recipients_count,
                manual_retry=manual_retry,
                error_summary=None,
            )

            logger.info(
                "report_delivery: отчёт за %s отправлен — recipients=%d",
                report_date,
                recipients_count,
            )

            return DeliveryResult(
                success=True,
                report_date=report_date,
                recipients_count=recipients_count,
                event_id=event_id,
                manual_retry=manual_retry,
            )

        except EwsMailerError as e:
            error_summary = str(e)[:100]  # Обрезаем для безопасности
            logger.error("report_delivery: ошибка EWS — %s", type(e).__name__)

            # Записываем событие ошибки
            event_id = await ReportDeliveryService._record_event(
                event_type=EventType.REPORT_DAILY_FAILED,
                report_date=report_date,
                recipients_count=recipients_count,
                manual_retry=manual_retry,
                error_summary=error_summary,
            )

            return DeliveryResult(
                success=False,
                report_date=report_date,
                recipients_count=recipients_count,
                error_summary=error_summary,
                event_id=event_id,
                manual_retry=manual_retry,
            )

        except Exception as e:
            error_summary = f"{type(e).__name__}"
            logger.exception("report_delivery: неожиданная ошибка")

            # Записываем событие ошибки
            event_id = await ReportDeliveryService._record_event(
                event_type=EventType.REPORT_DAILY_FAILED,
                report_date=report_date,
                recipients_count=recipients_count,
                manual_retry=manual_retry,
                error_summary=error_summary,
            )

            return DeliveryResult(
                success=False,
                report_date=report_date,
                recipients_count=recipients_count,
                error_summary=error_summary,
                event_id=event_id,
                manual_retry=manual_retry,
            )

    @staticmethod
    async def _record_event(
        event_type: EventType,
        report_date: date,
        recipients_count: int,
        manual_retry: bool,
        error_summary: str | None,
    ) -> int | None:
        """
        Записать событие отправки отчёта.

        Args:
            event_type: REPORT_DAILY_SENT или REPORT_DAILY_FAILED.
            report_date: Дата отчёта.
            recipients_count: Количество получателей.
            manual_retry: Ручная повторная отправка.
            error_summary: Краткое описание ошибки (если есть).

        Returns:
            ID события или None.
        """
        # Dedup key включает manual_retry чтобы разрешить повторную отправку
        dedup_suffix = "_manual" if manual_retry else ""
        dedup_key = f"{event_type.value}:{report_date.isoformat()}{dedup_suffix}"

        payload: dict[str, Any] = {
            "report_date": report_date.isoformat(),
            "recipients_count": recipients_count,
            "manual_retry": manual_retry,
        }
        if error_summary:
            payload["error_summary"] = error_summary

        return await EventsRepo.create_event(
            event_type=event_type,
            dedup_key=dedup_key,
            event_date=report_date,
            payload=payload,
            notify_channel=NotifyChannel.NONE,
        )

    @staticmethod
    async def retry_daily_report(report_date: date) -> DeliveryResult:
        """
        Повторно отправить ежедневный отчёт (manual retry).

        Args:
            report_date: Дата отчёта.

        Returns:
            DeliveryResult с результатом доставки.
        """
        return await ReportDeliveryService.send_daily_report(
            report_date=report_date,
            manual_retry=True,
        )

    @staticmethod
    async def send_monthly_report(
        year: int,
        month: int,
        manual_retry: bool = False,
    ) -> MonthlyDeliveryResult:
        """
        Отправить месячный отчёт по email.

        Args:
            year: Год отчёта.
            month: Месяц отчёта (1-12).
            manual_retry: True если это ручная повторная отправка.

        Returns:
            MonthlyDeliveryResult с результатом доставки.
        """
        to_list = _get_report_to()
        cc_list = _get_report_cc()
        recipients_count = len(to_list) + len(cc_list)

        if not to_list:
            error_msg = "REPORT_TO не задан"
            logger.warning("monthly_report_delivery: %s", error_msg)
            return MonthlyDeliveryResult(
                success=False,
                year=year,
                month=month,
                recipients_count=0,
                error_summary=error_msg,
                manual_retry=manual_retry,
            )

        if not is_ews_configured():
            error_msg = "EWS не настроен"
            logger.warning("monthly_report_delivery: %s", error_msg)
            return MonthlyDeliveryResult(
                success=False,
                year=year,
                month=month,
                recipients_count=recipients_count,
                error_summary=error_msg,
                manual_retry=manual_retry,
            )

        try:
            # 1. Формируем отчёт
            report = await ReportsService.build_monthly_report_ozko(year, month)

            # 2. Генерируем XLSX
            xlsx_bytes = build_monthly_report_xlsx(report)

            # 3. Формируем тему письма
            # Шаблон из env или default
            import os
            subject_template = os.getenv(
                "REPORT_MONTHLY_SUBJECT_TEMPLATE",
                "SCREW Monthly report — {year}-{month:02d}",
            )
            subject = subject_template.format(year=year, month=month)

            # 4. Формируем тело письма
            month_names = [
                "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
            ]
            month_name = month_names[month] if 1 <= month <= 12 else str(month)
            body_text = f"Месячный отчёт ОЗКО за {month_name} {year}.\n\nДетали во вложении."

            # 5. Создаём вложение
            filename = f"report_monthly_ozko_{year}-{month:02d}.xlsx"
            attachment = Attachment(
                filename=filename,
                content=xlsx_bytes,
                mime_type=XLSX_MIME_TYPE,
            )

            # 6. Отправляем
            mailer = EwsMailer()
            try:
                mailer.send(
                    to=to_list,
                    cc=cc_list,
                    subject=subject,
                    body_text=body_text,
                    attachments=[attachment],
                )
            finally:
                mailer.close()

            # 7. Записываем событие успеха
            event_id = await ReportDeliveryService._record_monthly_event(
                event_type=EventType.REPORT_MONTHLY_SENT,
                year=year,
                month=month,
                recipients_count=recipients_count,
                manual_retry=manual_retry,
                error_summary=None,
            )

            logger.info(
                "monthly_report_delivery: отчёт за %d-%02d отправлен — recipients=%d",
                year,
                month,
                recipients_count,
            )

            return MonthlyDeliveryResult(
                success=True,
                year=year,
                month=month,
                recipients_count=recipients_count,
                event_id=event_id,
                manual_retry=manual_retry,
            )

        except EwsMailerError as e:
            error_summary = str(e)[:100]  # Обрезаем для безопасности
            logger.error("monthly_report_delivery: ошибка EWS — %s", type(e).__name__)

            # Записываем событие ошибки
            event_id = await ReportDeliveryService._record_monthly_event(
                event_type=EventType.REPORT_MONTHLY_FAILED,
                year=year,
                month=month,
                recipients_count=recipients_count,
                manual_retry=manual_retry,
                error_summary=error_summary,
            )

            return MonthlyDeliveryResult(
                success=False,
                year=year,
                month=month,
                recipients_count=recipients_count,
                error_summary=error_summary,
                event_id=event_id,
                manual_retry=manual_retry,
            )

        except Exception as e:
            error_summary = f"{type(e).__name__}"
            logger.exception("monthly_report_delivery: неожиданная ошибка")

            # Записываем событие ошибки
            event_id = await ReportDeliveryService._record_monthly_event(
                event_type=EventType.REPORT_MONTHLY_FAILED,
                year=year,
                month=month,
                recipients_count=recipients_count,
                manual_retry=manual_retry,
                error_summary=error_summary,
            )

            return MonthlyDeliveryResult(
                success=False,
                year=year,
                month=month,
                recipients_count=recipients_count,
                error_summary=error_summary,
                event_id=event_id,
                manual_retry=manual_retry,
            )

    @staticmethod
    async def _record_monthly_event(
        event_type: EventType,
        year: int,
        month: int,
        recipients_count: int,
        manual_retry: bool,
        error_summary: str | None,
    ) -> int | None:
        """
        Записать событие отправки месячного отчёта.

        Args:
            event_type: REPORT_MONTHLY_SENT или REPORT_MONTHLY_FAILED.
            year: Год отчёта.
            month: Месяц отчёта.
            recipients_count: Количество получателей.
            manual_retry: Ручная повторная отправка.
            error_summary: Краткое описание ошибки (если есть).

        Returns:
            ID события или None.
        """
        # Dedup key включает manual_retry чтобы разрешить повторную отправку
        dedup_suffix = "_manual" if manual_retry else ""
        dedup_key = f"{event_type.value}:{year}-{month:02d}{dedup_suffix}"

        payload: dict[str, Any] = {
            "year": year,
            "month": month,
            "recipients_count": recipients_count,
            "manual_retry": manual_retry,
        }
        if error_summary:
            payload["error_summary"] = error_summary

        # Используем первый день месяца как event_date
        from datetime import date as date_type
        event_date = date_type(year, month, 1)

        return await EventsRepo.create_event(
            event_type=event_type,
            dedup_key=dedup_key,
            event_date=event_date,
            payload=payload,
            notify_channel=NotifyChannel.NONE,
        )

    @staticmethod
    async def retry_monthly_report(year: int, month: int) -> MonthlyDeliveryResult:
        """
        Повторно отправить месячный отчёт (manual retry).

        Позволяет отправить отчёт даже если SENT событие уже существует.

        Args:
            year: Год отчёта.
            month: Месяц отчёта (1-12).

        Returns:
            MonthlyDeliveryResult с результатом доставки.
        """
        return await ReportDeliveryService.send_monthly_report(
            year=year,
            month=month,
            manual_retry=True,
        )
