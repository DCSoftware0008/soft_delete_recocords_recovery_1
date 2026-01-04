from odoo import fields, models, api, _, SUPERUSER_ID
from odoo.exceptions import UserError, ValidationError
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
            record.is_soft_delete_module = record.module_id.name == 'soft_delete'

    @api.depends('module_ids', 'module_id')
    def _compute_model_ids(self):
        """
        Compute the model_ids field, handling both the default case and the special case
        for the soft_delete module.
        """
        for wizard in self:
            if not wizard.module_id:
                wizard.model_ids = [(6, 0, [])]
                continue

            if wizard.module_id.name == 'soft_delete':
                # Get base models from soft.delete.manager.config
                config = self.env['soft.delete.manager.config'].search([], limit=1)
                wizard_model_names = []
                if config and config.model_ids:
                    wizard_model_names = [
                        'x_%s_wizard' % model.model.replace('.', '_')
                        for model in config.model_ids
                    ]
                wizard_models = self.env['ir.model'].search([
                    ('transient', '=', False),
                    ('model', 'in', wizard_model_names)
                ])

                # Properly update name field to readable version
                for model in wizard_models:
                    if model.model.startswith('x_') and model.name == model.model:
                        readable = model.model.replace('x_', '').replace('_wizard', '').replace('_', ' ').title()
                        model.write({'name': readable})

                # Validate that transformed models have the 'x_is_deleted' field
                for model in wizard_models:
                    model_name = model.model
                    # Transform the model name directly for validation
                    if model_name.startswith('x_') and model_name.endswith('_wizard'):
                        transformed_name = model_name[2:-7].replace('_', '.')  # Remove 'x_' and '_wizard', convert to Odoo format
                        if transformed_name in self.env and 'x_is_deleted' not in self.env[transformed_name]._fields:
                            _logger.warning(
                                f"Transformed model '{transformed_name}' (from '{model_name}') is missing the 'x_is_deleted' field required for soft deletion."
                            )
                    else:
                        if model_name in self.env and 'x_is_deleted' not in self.env[model_name]._fields:
                            _logger.warning(
                                f"Model '{model_name}' is missing the 'x_is_deleted' field required for soft deletion."
                            )

                wizard.model_ids = wizard_models
            else:
                # Default logic for other modules
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

        # Fetch Boolean config value without default fallback
        param_bool = ICPSudo.get_param('soft_delete.select_all_permanent_delete')
        _logger.info(f"Fetched select_all_permanent_delete from config param: {param_bool}")
        res['select_all_permanent_delete'] = (param_bool == 'True')

        # Fetch Many2many config value with default fallback
        ids_str = ICPSudo.get_param('soft_delete.specific_models_recover', default='')
        model_ids = [int(id) for id in ids_str.split(',') if id]
        res['specific_models_recover'] = [(6, 0, model_ids)]

        return res

    @api.onchange('specific_models_recover')
    def _onchange_specific_models_recover(self):
        if self.specific_models_recover:
            self.select_all_permanent_delete = False
        else:
            self.select_all_permanent_delete = True


