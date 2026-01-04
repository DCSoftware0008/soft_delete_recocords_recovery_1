from odoo import models, fields, tools
import logging

_logger = logging.getLogger(__name__)

class SoftDeleteManagerAllModules(models.Model):
    _name = 'soft.delete.manager.all.modules'
    _description = 'Soft Delete Manager All Modules'
    _auto = False  # View-based model

    model_id = fields.Many2one('ir.model', string='Wizard Model', readonly=True)
    model_count = fields.Integer(string='Record Count', readonly=True, compute='_compute_model_count')
    record_count_for_pivot = fields.Integer(string="Pivot Count", readonly=True)
    model_display_name = fields.Char(string='Model Name', compute='_compute_model_display_name', readonly=True)

    def _query(self):
        cr = self._cr
        cr.execute("""
            SELECT id, model FROM ir_model
            WHERE model LIKE 'x_%%_wizard'
        """)
        rows = cr.fetchall()

        union_sql = []
        row_id = 1
        for model_id, model_name in rows:
            table_name = model_name.replace('.', '_')
            try:
                # Check if table exists
                cr.execute("""
                    SELECT COUNT(*) FROM information_schema.tables 
                    WHERE table_name = %s
                """, [table_name])
                if cr.fetchone()[0] == 1:
                    cr.execute(f"SELECT COUNT(*) FROM {table_name}")
                    count = cr.fetchone()[0]
                else:
                    count = 0
            except Exception as e:
                _logger.warning(f"⚠️ Cannot count rows for model: {model_name} (table: {table_name}): {e}")
                count = 0

            union_sql.append(f"""
                SELECT {row_id} AS id,
                       CAST({model_id} AS INTEGER) AS model_id,
                       {int(count)} AS record_count_for_pivot
            """)
            row_id += 1

        if not union_sql:
            union_sql.append("SELECT 1 AS id, NULL::INTEGER AS model_id, 0 AS record_count_for_pivot")

        return " UNION ALL ".join(union_sql)


    def init(self):
        tools.drop_view_if_exists(self._cr, self._table)
        view_sql = self._query()
        self._cr.execute(f"CREATE OR REPLACE VIEW {self._table} AS ({view_sql})")

    def _compute_model_display_name(self):
        for record in self:
            if record.model_id and record.model_id.model:  # Use model_id.model instead of model_id.name
                base_name = record.model_id.model  # e.g., 'x_cargo_short_name_master_wizard'
                # Remove 'x_' prefix and '_wizard' suffix, then replace '_' with ' '
                cleaned_name = base_name
                if base_name.startswith('x_'):
                    cleaned_name = cleaned_name[2:]  # Remove 'x_'
                if cleaned_name.endswith('_wizard'):
                    cleaned_name = cleaned_name[:-7]  # Remove '_wizard'
                display_name = cleaned_name.replace('_', ' ')  # Replace '_' with ' '
                record.model_display_name = display_name.title()  # Optional: Capitalize words
            else:
                record.model_display_name = False

    def _compute_model_count(self):
        for record in self:
            record.model_count = record.record_count_for_pivot
