from __future__ import annotations

from io import BytesIO

import xlsxwriter
from xlsxwriter.format import Format

from application.services.finance_exports import FinanceExportReport
from domain.currencies import currency_prefix
from domain.enums import PlannedExpenseRecurrence, PlannedExpenseStatus, TransactionType

NAVY = "#132238"
NAVY_LIGHT = "#1D314B"
PURPLE = "#A970FF"
GREEN = "#42D6A4"
RED = "#FF7474"
YELLOW = "#F4C95D"
BLUE = "#58ACFF"
WHITE = "#F5F7FC"
MUTED = "#A4B1C6"

TYPE_LABELS = {
    TransactionType.EXPENSE: "Витрата",
    TransactionType.INCOME: "Дохід",
}
PLAN_STATUS_LABELS = {
    PlannedExpenseStatus.PENDING: "Очікує підтвердження",
    PlannedExpenseStatus.SCHEDULED: "Заплановано",
    PlannedExpenseStatus.PAID: "Сплачено",
    PlannedExpenseStatus.CANCELLED: "Скасовано",
}


class XlsxFinanceReportRenderer:
    @staticmethod
    def _formats(workbook: xlsxwriter.Workbook, currency: str) -> dict[str, Format]:
        symbol = currency_prefix(currency)
        return {
            "title": workbook.add_format(
                {
                    "bold": True,
                    "font_size": 24,
                    "font_color": WHITE,
                    "bg_color": NAVY,
                    "align": "left",
                    "valign": "vcenter",
                }
            ),
            "subtitle": workbook.add_format(
                {"font_size": 11, "font_color": MUTED, "bg_color": NAVY}
            ),
            "label": workbook.add_format(
                {"font_color": MUTED, "bg_color": NAVY_LIGHT, "bold": True}
            ),
            "value": workbook.add_format(
                {
                    "font_size": 16,
                    "font_color": WHITE,
                    "bg_color": NAVY_LIGHT,
                    "bold": True,
                    "num_format": f'{symbol}#,##0.00;[Red]-{symbol}#,##0.00',
                }
            ),
            "header": workbook.add_format(
                {
                    "bold": True,
                    "font_color": WHITE,
                    "bg_color": NAVY_LIGHT,
                    "border": 0,
                    "align": "center",
                    "valign": "vcenter",
                }
            ),
            "text": workbook.add_format({"valign": "top"}),
            "date": workbook.add_format({"num_format": "dd.mm.yyyy", "align": "center"}),
            "month": workbook.add_format({"num_format": "mmmm yyyy", "align": "left"}),
            "money": workbook.add_format(
                {"num_format": f'{symbol}#,##0.00;[Red]-{symbol}#,##0.00'}
            ),
            "percent": workbook.add_format({"num_format": "0.0%"}),
            "income": workbook.add_format(
                {
                    "font_color": "#167C5A",
                    "num_format": f'{symbol}#,##0.00;[Red]-{symbol}#,##0.00',
                }
            ),
            "expense": workbook.add_format(
                {
                    "font_color": "#C64747",
                    "num_format": f'{symbol}#,##0.00;[Red]-{symbol}#,##0.00',
                }
            ),
            "note": workbook.add_format({"font_color": MUTED, "italic": True}),
        }

    @staticmethod
    def _prepare_sheet(worksheet, *, widths: tuple[float, ...]) -> None:
        worksheet.hide_gridlines(2)
        worksheet.freeze_panes(1, 0)
        for column, width in enumerate(widths):
            worksheet.set_column(column, column, width)

    @staticmethod
    def _write_headers(worksheet, headers: tuple[str, ...], header: Format) -> None:
        worksheet.set_row(0, 26)
        for column, value in enumerate(headers):
            worksheet.write_string(0, column, value, header)
        worksheet.autofilter(0, 0, 0, len(headers) - 1)

    def _summary(
        self,
        workbook: xlsxwriter.Workbook,
        report: FinanceExportReport,
        formats: dict[str, Format],
    ) -> None:
        sheet = workbook.add_worksheet("Підсумок")
        sheet.hide_gridlines(2)
        sheet.set_tab_color(PURPLE)
        sheet.set_column("A:A", 3)
        sheet.set_column("B:G", 18)
        sheet.set_default_row(22)
        sheet.set_row(1, 42)
        sheet.merge_range("B2:G2", f"Сімейний бюджет • {report.period.label}", formats["title"])
        sheet.merge_range(
            "B3:G3",
            f"{report.household_name} · Валюта: {report.currency} · "
            f"Створено {report.created_at:%d.%m.%Y %H:%M} UTC",
            formats["subtitle"],
        )

        cards = (
            ("B5:C5", "B6:C7", "Доходи", report.income, GREEN),
            ("D5:E5", "D6:E7", "Витрати", report.expense, RED),
            ("F5:G5", "F6:G7", "Баланс", report.balance, BLUE),
            ("B9:C9", "B10:C11", "Заплановано", report.planned_total, YELLOW),
            (
                "D9:G9",
                "D10:G11",
                "Доступно після планів",
                report.available_after_plans,
                PURPLE,
            ),
        )
        for label_range, value_range, label, value, color in cards:
            label_format = workbook.add_format(
                {
                    "font_color": MUTED,
                    "bg_color": NAVY_LIGHT,
                    "bold": True,
                    "top": 2,
                    "top_color": color,
                }
            )
            sheet.merge_range(label_range, label, label_format)
            sheet.merge_range(value_range, float(value), formats["value"])

        difference = report.expense - report.previous_expense
        if report.previous_expense == 0:
            comparison = "Немає попереднього періоду для порівняння"
        elif difference < 0:
            comparison = (
                f"Витрати зменшилися на "
                f"{currency_prefix(report.currency)}{abs(difference):.2f}"
            )
        elif difference > 0:
            comparison = (
                f"Витрати зросли на {currency_prefix(report.currency)}{difference:.2f}"
            )
        else:
            comparison = "Витрати не змінилися"
        sheet.merge_range("B13:G13", comparison, formats["subtitle"])

        expense_rows = [
            index
            for index, category in enumerate(report.categories, start=1)
            if category.type is TransactionType.EXPENSE
        ]
        if expense_rows:
            chart = workbook.add_chart({"type": "doughnut"})
            chart.add_series(
                {
                    "name": "Витрати за категоріями",
                    "categories": ["Категорії", expense_rows[0], 0, expense_rows[-1], 0],
                    "values": ["Категорії", expense_rows[0], 2, expense_rows[-1], 2],
                    "data_labels": {"percentage": True, "leader_lines": True},
                    "points": [
                        {"fill": {"color": color}}
                        for color in (GREEN, RED, YELLOW, BLUE, PURPLE)
                    ],
                }
            )
            chart.set_hole_size(58)
            chart.set_title({"name": "Структура витрат"})
            chart.set_legend({"position": "bottom"})
            chart.set_chartarea({"border": {"none": True}})
            sheet.insert_chart("B15", chart, {"x_scale": 1.3, "y_scale": 1.15})

        if report.period.month_count > 1:
            chart = workbook.add_chart({"type": "column"})
            last_row = len(report.months)
            chart.add_series(
                {
                    "name": "Доходи",
                    "categories": ["По місяцях", 1, 0, last_row, 0],
                    "values": ["По місяцях", 1, 1, last_row, 1],
                    "fill": {"color": GREEN},
                }
            )
            chart.add_series(
                {
                    "name": "Витрати",
                    "categories": ["По місяцях", 1, 0, last_row, 0],
                    "values": ["По місяцях", 1, 2, last_row, 2],
                    "fill": {"color": RED},
                }
            )
            chart.set_title({"name": "Динаміка за місяцями"})
            chart.set_legend({"position": "bottom"})
            chart.set_chartarea({"border": {"none": True}})
            sheet.insert_chart("E15", chart, {"x_scale": 1.25, "y_scale": 1.15})

    def _transactions(
        self, workbook: xlsxwriter.Workbook, report: FinanceExportReport, formats: dict[str, Format]
    ) -> None:
        sheet = workbook.add_worksheet("Операції")
        self._prepare_sheet(sheet, widths=(13, 12, 24, 42, 22, 16))
        headers = ("Дата", "Тип", "Категорія", "Опис", "Учасник", "Сума")
        self._write_headers(sheet, headers, formats["header"])
        for row, item in enumerate(report.transactions, start=1):
            sheet.write_datetime(row, 0, item.transaction_date, formats["date"])
            sheet.write_string(row, 1, TYPE_LABELS[item.type], formats["text"])
            sheet.write_string(row, 2, item.category, formats["text"])
            sheet.write_string(row, 3, item.description, formats["text"])
            sheet.write_string(row, 4, item.member, formats["text"])
            sheet.write_number(
                row,
                5,
                float(item.amount),
                formats["income"] if item.type is TransactionType.INCOME else formats["expense"],
            )
        if report.transactions:
            sheet.autofilter(0, 0, len(report.transactions), len(headers) - 1)

    def _categories(
        self, workbook: xlsxwriter.Workbook, report: FinanceExportReport, formats: dict[str, Format]
    ) -> None:
        sheet = workbook.add_worksheet("Категорії")
        self._prepare_sheet(sheet, widths=(30, 14, 18, 14))
        headers = ("Категорія", "Тип", "Сума", "Частка")
        self._write_headers(sheet, headers, formats["header"])
        for row, item in enumerate(report.categories, start=1):
            sheet.write_string(row, 0, item.name, formats["text"])
            sheet.write_string(row, 1, TYPE_LABELS[item.type], formats["text"])
            sheet.write_number(row, 2, float(item.amount), formats["money"])
            sheet.write_number(row, 3, float(item.percentage), formats["percent"])
        if report.categories:
            sheet.autofilter(0, 0, len(report.categories), len(headers) - 1)

    def _monthly(
        self, workbook: xlsxwriter.Workbook, report: FinanceExportReport, formats: dict[str, Format]
    ) -> None:
        if report.period.month_count <= 1:
            return
        sheet = workbook.add_worksheet("По місяцях")
        self._prepare_sheet(sheet, widths=(22, 18, 18, 18))
        headers = ("Місяць", "Доходи", "Витрати", "Баланс")
        self._write_headers(sheet, headers, formats["header"])
        for row, item in enumerate(report.months, start=1):
            sheet.write_datetime(row, 0, item.month, formats["month"])
            sheet.write_number(row, 1, float(item.income), formats["income"])
            sheet.write_number(row, 2, float(item.expense), formats["expense"])
            sheet.write_number(row, 3, float(item.balance), formats["money"])

    def _plans(
        self, workbook: xlsxwriter.Workbook, report: FinanceExportReport, formats: dict[str, Format]
    ) -> None:
        sheet = workbook.add_worksheet("Заплановані витрати")
        self._prepare_sheet(sheet, widths=(13, 25, 42, 22, 18, 22, 16))
        headers = (
            "Дата",
            "Категорія",
            "Опис",
            "Учасник",
            "Повторення",
            "Статус",
            "Сума",
        )
        self._write_headers(sheet, headers, formats["header"])
        if not report.planned_expenses:
            sheet.merge_range("A2:G2", "За цей період запланованих витрат немає.", formats["note"])
            return
        for row, item in enumerate(report.planned_expenses, start=1):
            recurrence = (
                "Щомісяця"
                if item.recurrence is PlannedExpenseRecurrence.MONTHLY
                else "Не повторюється"
            )
            sheet.write_datetime(row, 0, item.due_date, formats["date"])
            sheet.write_string(row, 1, item.category, formats["text"])
            sheet.write_string(row, 2, item.description, formats["text"])
            sheet.write_string(row, 3, item.member, formats["text"])
            sheet.write_string(row, 4, recurrence, formats["text"])
            sheet.write_string(row, 5, PLAN_STATUS_LABELS[item.status], formats["text"])
            sheet.write_number(row, 6, float(item.amount), formats["money"])
        sheet.autofilter(0, 0, len(report.planned_expenses), len(headers) - 1)

    def render(self, report: FinanceExportReport) -> bytes:
        output = BytesIO()
        workbook = xlsxwriter.Workbook(
            output,
            {
                "in_memory": True,
                "strings_to_formulas": False,
                "strings_to_urls": False,
                "remove_timezone": True,
            },
        )
        try:
            workbook.set_properties(
                {
                    "title": f"Сімейний бюджет — {report.period.label}",
                    "subject": "Фінансовий звіт",
                    "author": "FamilyBudgetBot",
                }
            )
            formats = self._formats(workbook, report.currency)
            self._summary(workbook, report, formats)
            self._transactions(workbook, report, formats)
            self._categories(workbook, report, formats)
            self._monthly(workbook, report, formats)
            self._plans(workbook, report, formats)
        finally:
            workbook.close()
        return output.getvalue()


__all__ = ["XlsxFinanceReportRenderer"]
