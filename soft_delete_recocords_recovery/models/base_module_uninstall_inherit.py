from odoo import fields, models, api, _
import logging

_logger = logging.getLogger(__name__)

class BaseModuleUninstall(models.TransientModel):
    _inherit = 'base.module.uninstall'

    select_all_permanent_delete = fields.Boolean(
        string='Select All Models for Deleted Records Permanently Delete'
    )

    specific_models_recover = fields.Many2many(
        'ir.model',
        string='Select Specific Model for Records Recover',
        relation='uninstall_specific_models_rel',
    )

    is_soft_delete_module = fields.Boolean(
        string="Is Soft Delete Module",
        compute='_compute_is_soft_delete_module'
    )

    @api.depends('module_id')
    def _compute_is_soft_delete_module(self):
        for record in self:
            record.is_soft_delete_module = record.module_id.name == 'soft_delete_recocords_recovery'

    @api.depends('module_ids', 'module_id')
    def _compute_model_ids(self):
        """
        Compute the model_ids field.
        When uninstalling this module (soft_delete_recocords_recovery),
        show only the dynamic wizard models (x_..._wizard) that were created.
        """
        for wizard in self:
            if not wizard.module_id:
                wizard.model_ids = [(6, 0, [])]
                continue

            if wizard.module_id.name == 'soft_delete_recocords_recovery':
                # Get all wizard models created by this module: x_*_wizard
                wizard_models = self.env['ir.model'].search([
                    ('model', '=like', 'x_%_wizard'),
                    ('state', '=', 'manual'),  # Only manually created (dynamic) models
                    ('transient', '=', True),  # Wizards are transient
                ])

                # Improve display name: x_cargo_short_name_master_wizard → Cargo Short Name Master
                for model in wizard_models:
                    if model.name == model.model:  # Default technical name
                        readable = (
                            model.model[2:-7]  # Remove 'x_' and '_wizard'
                            .replace('_', ' ')
                            .title()
                        )
                        model.write({'name': readable})

                wizard.model_ids = wizard_models
            else:
                # Default Odoo behavior for other modules
                ir_models = self._get_models()
                ir_models_xids = ir_models._get_external_ids()
                module_names = set(wizard._get_modules().mapped('name'))

                def lost(model):
                    xids = ir_models_xids.get(model.id, ())
                    return xids and all(xid.split('.')[0] in module_names for xid in xids)

                wizard.model_ids = ir_models.filtered(lost).sorted('name')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ICPSudo = self.env['ir.config_parameter'].sudo()

        # Load saved preferences from config parameters
        param_bool = ICPSudo.get_param('soft_delete_recocords_recovery.select_all_permanent_delete')
        res['select_all_permanent_delete'] = (param_bool == 'True')

        ids_str = ICPSudo.get_param('soft_delete_recocords_recovery.specific_models_recover', default='')
        model_ids = [int(id_) for id_ in ids_str.split(',') if id_.strip().isdigit()]
        res['specific_models_recover'] = [(6, 0, model_ids)]

        return res

    @api.onchange('specific_models_recover')
    def _onchange_specific_models_recover(self):
        if self.specific_models_recover:
            self.select_all_permanent_delete = False
        else:
            self.select_all_permanent_delete = True
