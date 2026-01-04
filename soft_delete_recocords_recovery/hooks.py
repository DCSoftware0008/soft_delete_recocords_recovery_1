from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)

def uninstall_hook(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _logger.info("Running soft delete uninstall cleanup")

    try:
        ICPSudo = env['ir.config_parameter'].sudo()

        # Get saved values
        model_ids_str = ICPSudo.get_param('soft_delete_recocords_recovery.model_ids', '')
        all_model_ids = [int(x) for x in model_ids_str.split(',') if x.strip().isdigit()]

        recover_ids_str = ICPSudo.get_param('soft_delete_recocords_recovery.specific_models_recover', '')
        recover_model_ids = [int(x) for x in recover_ids_str.split(',') if x.strip().isdigit()]

        # Recover selected
        for model in env['ir.model'].browse(recover_model_ids):
            if model.model in env and 'x_is_deleted' in env[model.model]._fields:
                env[model.model].sudo().search([('x_is_deleted', '=', True)]).write({'x_is_deleted': False})

        # Permanent delete others
        for model in env['ir.model'].browse(all_model_ids):
            if model.id in recover_model_ids:
                continue
            if model.model in env and 'x_is_deleted' in env[model.model]._fields:
                records = env[model.model].sudo().search([('x_is_deleted', '=', True)])
                if records:
                    try:
                        records.unlink()
                    except:
                        table = model.model.replace('.', '_')
                        cr.execute(f"DELETE FROM {table} WHERE x_is_deleted = TRUE")

        # Run cleanup
        env['res.config.settings'].sudo().create({}).action_cleanup_soft_delete()

    except Exception as e:
        _logger.error(f"Uninstall hook error: {e}")
        raise