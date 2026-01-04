from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)

def uninstall_hook(cr, registry):
    """
    Hook to handle soft delete logic during uninstallation of the soft_delete module.
    Recovers records for selected models, permanently deletes records for non-selected
    models based on the x_is_deleted field, and calls action_cleanup_soft_delete at the end.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    _logger.info("Uninstalling soft_delete module with custom logic")

    try:
        # Get all models from soft.delete.manager.config
        config = env['soft.delete.manager.config'].search([], limit=1)
        if not config or not config.model_ids:
            _logger.warning("No models configured in soft.delete.manager.config, proceeding to cleanup")
        else:
            all_models = config.model_ids

            # Fetch specific_models_recover from ir.config_parameter
            ids_str = env['ir.config_parameter'].sudo().get_param('soft_delete.specific_models_recover', default='')
            selected_model_ids = [int(id) for id in ids_str.split(',') if id]
            non_selected_models = all_models.filtered(lambda m: m.id not in selected_model_ids)

            # Step 1: Recover records for selected models (set x_is_deleted = False)
            selected_models = env['ir.model'].browse(selected_model_ids)
            for model in selected_models:
                model_name = model.model
                if model_name in env and 'x_is_deleted' in env[model_name]._fields:
                    try:
                        records = env[model_name].sudo().search([('x_is_deleted', '=', True)])
                        if records:
                            records.write({'x_is_deleted': False})
                            # _logger.info(f"Recovered {len(records)} records in model {model_name} by setting x_is_deleted = False")
                        else:
                            _logger.info(f"No soft-deleted records found in model {model_name}")
                    except Exception as e:
                        _logger.error(f"Failed to recover records in model {model_name}: {str(e)}")
                else:
                    _logger.warning(f"Model {model_name} does not exist or lacks x_is_deleted field")

            # Step 2: Permanently delete records for non-selected models
            for model in non_selected_models:
                model_name = model.model
                if model_name not in env:
                    _logger.warning(f"Model {model_name} does not exist in the environment")
                    continue
                if 'x_is_deleted' not in env[model_name]._fields:
                    _logger.warning(f"Model {model_name} lacks x_is_deleted field")
                    continue

                try:
                    records = env[model_name].sudo().search([('x_is_deleted', '=', True)])
                    if not records:
                        _logger.info(f"No soft-deleted records found in model {model_name}")
                        continue

                    _logger.info(f"Attempting to delete {len(records)} records in model {model_name}")
                    try:
                        # Attempt ORM unlink
                        records.with_context(_force_unlink=True).unlink()
                        _logger.info(f"Successfully deleted {len(records)} records in model {model_name} via ORM")
                    except Exception as e:
                        _logger.warning(f"ORM unlink failed for model {model_name}: {str(e)}. Attempting SQL deletion.")
                        # Fallback to direct SQL deletion
                        table_name = model_name.replace('.', '_')
                        try:
                            cr.execute(
                                f"DELETE FROM {table_name} WHERE x_is_deleted = TRUE"
                            )
                            deleted_count = cr.rowcount
                            _logger.info(f"Successfully deleted {deleted_count} records in model {model_name} via SQL")
                        except Exception as sql_e:
                            _logger.error(f"SQL deletion failed for model {model_name}: {str(sql_e)}")

                except Exception as e:
                    _logger.error(f"Failed to process records in model {model_name}: {str(e)}")

        # Step 3: Call action_cleanup_soft_delete from res.config.settings
        try:
            config_settings = env['res.config.settings'].sudo().create({})
            config_settings.action_cleanup_soft_delete()
            _logger.info("Successfully executed action_cleanup_soft_delete")
        except Exception as e:
            _logger.error(f"Failed to execute action_cleanup_soft_delete: {str(e)}")
            raise

        _logger.info("Successfully executed uninstall cleanup")
    except Exception as e:
        _logger.error(f"Error during uninstall cleanup: {str(e)}")
        raise