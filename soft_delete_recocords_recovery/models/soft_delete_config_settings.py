from odoo import models, fields, api, _, SUPERUSER_ID
import logging
from lxml import etree
from odoo.exceptions import AccessError
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class SoftDeleteConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    model_ids = fields.Many2many(
        'ir.model',
        string="Model Name",
        domain="[('model', '!=', False)]",
        readonly=False
    )

    # config_id = fields.Many2one(
    #     'soft.delete.manager.config',
    #     string="Configuration",
    #     # required=True,
    # )

    select_all_permanent_delete = fields.Boolean(
        string='Select All Models for Deleted Records Permanently Delete',
        default=True
    )

    specific_models_recover = fields.Many2many(
        'ir.model',
        string='Select Specific Model for Records Recover',
        relation='soft_delete_specific_models_rel'
    )

    def get_values(self):
        res = super().get_values()
        ICPSudo = self.env['ir.config_parameter'].sudo()

        # Load saved models
        model_ids_str = ICPSudo.get_param('soft_delete_recocords_recovery.model_ids', default='')
        model_ids = [int(x) for x in model_ids_str.split(',') if x.strip().isdigit()]

        select_all = ICPSudo.get_param('soft_delete_recocords_recovery.select_all_permanent_delete', 'True') == 'True'

        recover_ids_str = ICPSudo.get_param('soft_delete_recocords_recovery.specific_models_recover', default='')
        recover_ids = [int(x) for x in recover_ids_str.split(',') if x.strip().isdigit()]

        res.update({
            # 'model_ids': [(6, 0, model_ids)],
            'select_all_permanent_delete': select_all,
            'specific_models_recover': [(6, 0, recover_ids)],
        })
        return res

    def set_values(self):
        super().set_values()
        ICPSudo = self.env['ir.config_parameter'].sudo()

        # Save model_ids
        model_ids = self.model_ids.ids
        ICPSudo.set_param('soft_delete_recocords_recovery.model_ids', ','.join(map(str, model_ids)) or '')

        # Save other params
        ICPSudo.set_param('soft_delete_recocords_recovery.select_all_permanent_delete', str(self.select_all_permanent_delete))
        ICPSudo.set_param('soft_delete_recocords_recovery.specific_models_recover', ','.join(map(str, self.specific_models_recover.ids)) or '')

        previous_model_ids = self._get_previous_model_ids()
        new_model_ids = model_ids

        self._ensure_is_deleted_field(model_ids)

        # After _ensure_is_deleted_field(...)
        IrModel = self.env['ir.model']
        for model_rec in IrModel.browse(new_model_ids):
            model_name = model_rec.model
            if model_rec.transient:
                continue  # wizards shouldn't be soft-deleted anyway
            self._patch_unlink_method(model_name)

        # Apply view inheritances and js_class
        self._apply_view_inheritances_and_params(new_model_ids)

        self._apply_domain_to_actions(new_model_ids)

        # Ensure server actions and wizards
        for model in self.env['ir.model'].browse(new_model_ids):
            wizard_model_name = self._create_dynamic_wizard_model_and_view(model.model)
            self._ensure_server_action(model, wizard_model_name)

    def _get_previous_model_ids(self):
        ICPSudo = self.env['ir.config_parameter'].sudo()
        ids_str = ICPSudo.get_param('soft_delete_recocords_recovery.model_ids', default='')
        return [int(x) for x in ids_str.split(',') if x.strip().isdigit()]

    def _patch_unlink_method(self, model_name):
        """
        Safely monkey-patch unlink method for soft delete
        """
        try:
            # Get the actual model class from registry (most stable way)
            ModelClass = self.env.registry[model_name]

            # Already patched? Skip
            if getattr(ModelClass, '_soft_delete_patched', False):
                _logger.info(f"unlink already patched for {model_name}, skipping.")
                return

            original_unlink = ModelClass.unlink

            def patched_unlink(self, *args, **kwargs):
                # self here is a recordset
                if not self:
                    return True

                # Only soft-delete if the field exists
                if 'x_is_deleted' in self._fields:
                    # We write on all records at once (more efficient)
                    self.write({'x_is_deleted': True})
                    _logger.info(f"Soft-deleted {len(self)} records in {model_name}")
                else:
                    # Fallback to hard delete if field missing (shouldn't happen)
                    _logger.warning(f"x_is_deleted missing in {model_name} → hard delete")
                    return original_unlink(self, *args, **kwargs)

                return True  # Important: return True to mimic successful unlink

            # Replace the method
            ModelClass.unlink = patched_unlink
            ModelClass.unlink_original = original_unlink  # optional, for permanent delete later
            ModelClass._soft_delete_patched = True

            _logger.info(f"Successfully patched unlink for {model_name}")

        except Exception as e:
            _logger.error(f"Failed to patch unlink for {model_name}: {str(e)}", exc_info=True)
            # Optional: raise if you want to block saving config when patching fails
            # raise

    # def set_values(self):
    #     super().set_values()
    #     self.ensure_one()

    #     previous_model_ids = self.config_id.model_ids.ids
    #     new_model_ids = self.model_ids.ids
    #     _logger.info(f"Saving Soft Delete configuration: previous_model_ids={previous_model_ids}, new_model_ids={new_model_ids}")

    #     self.config_id.write({'model_ids': [(6, 0, new_model_ids)]})

    #     # Create dynamic wizards and ensure server actions for each selected model
    #     IrModel = self.env['ir.model']
    #     for model in IrModel.browse(new_model_ids):
    #         wizard_model_name = self._create_dynamic_wizard_model_and_view(model.model)
    #         self._ensure_server_action(model, wizard_model_name)

    #     # Apply domain to window actions
    #     self._apply_domain_to_actions(new_model_ids)

    #     # === NEW: Apply view inheritances and save parameters at the very end ===
    #     self._apply_view_inheritances_and_params(new_model_ids)

    def _ensure_is_deleted_field(self, model_ids):
        """
        Ensure x_is_deleted Boolean field exists on selected models
        """
        IrModelFields = self.env['ir.model.fields'].sudo()

        for model in self.env['ir.model'].browse(model_ids):

            # Skip transient models
            if model.transient:
                continue

            existing_field = IrModelFields.search([
                ('name', '=', 'x_is_deleted'),
                ('model', '=', model.model),
            ], limit=1)

            if not existing_field:
                _logger.info("Creating x_is_deleted field on model %s", model.model)

                created_field = IrModelFields.create({
                    'name': 'x_is_deleted',
                    'field_description': 'Is Deleted',
                    'ttype': 'boolean',
                    'model_id': model.id,
                    'model': model.model,
                    'store': True,
                    'readonly': False,
                    'required': False,
                    'copied': False,
                    'state': 'manual',
                })

                if created_field:
                    _logger.info(
                        "x_is_deleted field successfully created on model %s",
                        model.model
                    )

    def _apply_view_inheritances_and_params(self, new_model_ids):
        ICPSudo = self.env['ir.config_parameter'].sudo()
        ICPSudo.set_param('soft_delete_recocords_recovery.select_all_permanent_delete', self.select_all_permanent_delete)
        ICPSudo.set_param('soft_delete_recocords_recovery.specific_models_recover', ','.join(map(str, self.specific_models_recover.ids)))

        IrModel = self.env['ir.model']
        IrUiView = self.env['ir.ui.view']
        IrModelData = self.env['ir.model.data']
        IrActionsServer = self.env['ir.actions.server']

        all_models = IrModel.search([]).mapped('model')

        tree_view_names = [
            'x_soft_delete_manager.tree.view.inherit.dynamic',
            'x_soft_delete_manager.tree.view.x_is_deleted.inherit.dynamic',
            'x_soft_delete_manager.tree.view.js_class.inherit.dynamic',
        ]

        kanban_view_names = [
            'x_soft_delete_manager.kanban.view.inherit.dynamic',
            'x_soft_delete_manager.kanban.view.x_is_deleted.inherit.dynamic',
            'x_soft_delete_manager.kanban.view.js_class.inherit.dynamic',
        ]

        # Remove outdated inherited views (for all models, to avoid leftovers)
        existing_tree_views = IrUiView.search([
            ('inherit_id.model', 'in', all_models),
            ('name', 'in', tree_view_names),
        ])
        existing_tree_views.unlink()

        # 🔹 Remove existing KANBAN dynamic views
        existing_kanban_views = IrUiView.search([
            ('inherit_id.model', 'in', all_models),
            ('name', 'in', kanban_view_names),
        ])
        existing_kanban_views.unlink()

        # Process each selected model
        for model in IrModel.browse(new_model_ids):

            # --- Tree view inheritance (add js_class) ---
            tree_view = IrUiView.search([
                ('model', '=', model.model),
                ('type', '=', 'tree'),
                ('mode', '=', 'primary')
            ], limit=1)

            if tree_view:
                xml_id_record = IrModelData.search([
                    ('model', '=', 'ir.ui.view'),
                    ('res_id', '=', tree_view.id)
                ], limit=1)
                inherit_id_ref = xml_id_record.complete_name if xml_id_record else False

                try:
                    parser = etree.XMLParser(remove_blank_text=True)
                    tree = etree.fromstring(tree_view.arch_db, parser=parser)
                    current_js_class_nodes = tree.xpath("//tree/@js_class")
                    current_js_class = current_js_class_nodes[0] if current_js_class_nodes else ""
                except etree.ParseError as e:
                    _logger.error(f"Failed to parse XML for view {tree_view.id} of model {model.model}: {str(e)}")
                    current_js_class = ""

                new_js_class = current_js_class
                if "soft_delete_manager_list_view_with_button" not in current_js_class:
                    if current_js_class:
                        new_js_class = f"{current_js_class},soft_delete_manager_list_view_with_button"
                    else:
                        new_js_class = "soft_delete_manager_list_view_with_button"

                IrUiView.create({
                    'name': 'x_soft_delete_manager.tree.view.js_class.inherit.dynamic',
                    'model': model.model,
                    'type': 'tree',
                    'inherit_id': tree_view.id,
                    'mode': 'extension',
                    'arch': f"""
                        <xpath expr="//tree" position="attributes">
                            <attribute name="js_class">{new_js_class}</attribute>
                        </xpath>
                    """
                })
                _logger.info(f"Added js_class to tree view of model {model.model} (inherit_id: {tree_view.id}, external ref: {inherit_id_ref}, new js_class: {new_js_class})")
            else:
                _logger.warning(f"No primary tree view found for model {model.model}")

            # # --- tree view inheritance (add default_domain) ---
            # tree_view_field = IrUiView.search([
            #     ('model', '=', model.model),
            #     ('type', '=', 'tree'),
            #     ('mode', '=', 'primary')
            # ], limit=1)

            # # raise UserError(tree_view_field.name)

            # if tree_view_field:
            #     xml_id_record = IrModelData.search([
            #         ('model', '=', 'ir.ui.view'),
            #         ('res_id', '=', tree_view_field.id)
            #     ], limit=1)
            #     inherit_id_ref = xml_id_record.complete_name if xml_id_record else False

            #     # raise UserError(tree_view_field.name)

            #     IrUiView.create({
            #         'name': 'x_soft_delete_manager.tree.view.x_is_deleted.inherit.dynamic',
            #         'model': model.model,
            #         'type': 'tree',
            #         'inherit_id': tree_view_field.id,
            #         'mode': 'extension',
            #         'arch': """
            #             <xpath expr="//tree" position="attributes">
            #                 <attribute name="default_domain">[('x_is_deleted', '=', False)]</attribute>
            #             </xpath>
            #         """
            #     })
            #     _logger.info(f"Added domain to tree view of model {model.model} (inherit_id: {tree_view_field.id}, external ref: {inherit_id_ref})")
            # else:
            #     _logger.warning(f"No primary tree view found for model {model.model}")

            kanban_view = IrUiView.search([
                ('model', '=', model.model),
                ('type', '=', 'kanban'),
                ('mode', '=', 'primary')
            ], limit=1)

            if kanban_view:
                xml_id_record = IrModelData.search([
                    ('model', '=', 'ir.ui.view'),
                    ('res_id', '=', kanban_view.id)
                ], limit=1)
                inherit_id_ref = xml_id_record.complete_name if xml_id_record else False

                try:
                    parser = etree.XMLParser(remove_blank_text=True)
                    kanban = etree.fromstring(kanban_view.arch_db, parser=parser)
                    current_js_class_nodes = kanban.xpath("//kanban/@js_class")
                    current_js_class = current_js_class_nodes[0] if current_js_class_nodes else ""
                except etree.ParseError as e:
                    _logger.error(f"Failed to parse XML for view {kanban_view.id} of model {model.model}: {str(e)}")
                    current_js_class = ""

                new_js_class = current_js_class
                if "soft_delete_manager_kanban_view_with_button" not in current_js_class:
                    if current_js_class:
                        new_js_class = f"{current_js_class},soft_delete_manager_kanban_view_with_button"
                    else:
                        new_js_class = "soft_delete_manager_kanban_view_with_button"

                IrUiView.create({
                    'name': 'x_soft_delete_manager.kanban.view.js_class.inherit.dynamic',
                    'model': model.model,
                    'type': 'kanban',
                    'inherit_id': kanban_view.id,
                    'mode': 'extension',
                    'arch': f"""
                        <xpath expr="//kanban" position="attributes">
                            <attribute name="js_class">{new_js_class}</attribute>
                        </xpath>
                    """
                })
                _logger.info(f"Added js_class to kanban view of model {model.model} (inherit_id: {kanban_view.id}, external ref: {inherit_id_ref}, new js_class: {new_js_class})")
            else:
                _logger.warning(f"No primary kanban view found for model {model.model}")

            # # --- Kanban view inheritance (add default_domain) ---
            # kanban_view = IrUiView.search([
            #     ('model', '=', model.model),
            #     ('type', '=', 'kanban'),
            #     ('mode', '=', 'primary')
            # ], limit=1)

            # if kanban_view:
            #     xml_id_record = IrModelData.search([
            #         ('model', '=', 'ir.ui.view'),
            #         ('res_id', '=', kanban_view.id)
            #     ], limit=1)
            #     inherit_id_ref = xml_id_record.complete_name if xml_id_record else False

            #     IrUiView.create({
            #         'name': 'x_soft_delete_manager.kanban.view.x_is_deleted.inherit.dynamic',
            #         'model': model.model,
            #         'type': 'kanban',
            #         'inherit_id': kanban_view.id,
            #         'mode': 'extension',
            #         'arch': """
            #             <xpath expr="//kanban" position="attributes">
            #                 <attribute name="default_domain">[('x_is_deleted', '=', False)]</attribute>
            #             </xpath>
            #         """
            #     })
            #     _logger.info(f"Added domain to Kanban view of model {model.model} (inherit_id: {kanban_view.id}, external ref: {inherit_id_ref})")
            # else:
            #     _logger.warning(f"No primary Kanban view found for model {model.model}")

    def _ensure_server_action(self, model, wizard_model_name):
        """Ensure a server action exists for the given wizard model."""
        IrActionsServer = self.env['ir.actions.server']
        wizard_class_name = wizard_model_name.replace('.', '_')
        action_name = f"Populate {wizard_class_name} Records"

        _logger.debug(f"Checking for server action '{action_name}' for model {model.model}")
        existing_server_action = IrActionsServer.search([
            ('name', '=', action_name),
            ('model_id.model', '=', model.model),
        ], limit=1)

        if not existing_server_action:
            IrActionsServer.create({
                'name': action_name,
                'model_id': model.id,
                'state': 'code',
                'code': f"""
                    env['res.config.settings'].populate_wizard_records('{model.model}', '{wizard_model_name}')
                """,
            })
            _logger.info(f"Created server action '{action_name}' for model {model.model}")
        else:
            _logger.info(f"Server action '{action_name}' already exists for model {model.model} (ID: {existing_server_action.id})")

    @api.model
    def populate_wizard_records(self, model_name, wizard_model_name):
        """
        Populate the wizard with soft-deleted records of the given model.
        Called by the 'Populate ... Records' server action.
        """
        try:
            _logger.info(f"Populating wizard {wizard_model_name} for model {model_name}")

            model = self.env[model_name]
            wizard_model = self.env[wizard_model_name]
            ir_model = self.env['ir.model'].search([('model', '=', model_name)], limit=1)

            if not ir_model:
                raise ValueError(f"Model {model_name} not found in ir.model")

            # Get soft-deleted records
            deleted_records = model.with_context(active_test=False).search([('x_is_deleted', '=', True)])

            # Clear outdated wizard records
            existing_wizards = wizard_model.search([('x_model_id', '=', ir_model.id)])
            for wiz in existing_wizards:
                if not model.browse(wiz.x_record_id).exists() or not model.browse(wiz.x_record_id).x_is_deleted:
                    wiz.unlink()

            # Create new wizard entries
            vals_list = []
            for record in deleted_records:
                if not wizard_model.search([('x_model_id', '=', ir_model.id), ('x_record_id', '=', record.id)], limit=1):
                    vals_list.append({
                        'x_model_id': ir_model.id,
                        'x_record_id': record.id,
                        'x_display_name': record.display_name or f"Record {record.id}",
                    })

            if vals_list:
                wizard_model.create(vals_list)
                _logger.info(f"Created {len(vals_list)} wizard records for {wizard_model_name}")

        except Exception as e:
            _logger.error(f"Failed to populate wizard records for {model_name}: {e}")
            raise

    @api.model
    def restore_records(self, model_name, record_ids):
        """
        Restore soft-deleted records by setting x_is_deleted = False
        """
        try:
            records = self.env[model_name].browse(record_ids)
            if not records:
                return True

            records.write({'x_is_deleted': False})
            _logger.info(f"Restored {len(records)} records in {model_name}")

            # Clean up wizard entries
            wizard_model_name = f"x_{model_name.replace('.', '_')}_wizard"
            self.env[wizard_model_name].search([
                ('x_record_id', 'in', record_ids)
            ]).unlink()

            return True
        except Exception as e:
            _logger.error(f"Failed to restore records in {model_name}: {e}")
            raise

    @api.model
    def permanent_delete_records(self, model_name, record_ids):
        """
        Permanently delete soft-deleted records using original unlink
        """
        try:
            records = self.env[model_name].browse(record_ids)
            if not records:
                return True

            # Use the original unlink if patched
            if hasattr(records, 'unlink_original'):
                records.unlink_original()
            else:
                records.unlink()

            _logger.info(f"Permanently deleted {len(records)} records in {model_name}")

            # Clean up wizard entries
            wizard_model_name = f"x_{model_name.replace('.', '_')}_wizard"
            self.env[wizard_model_name].search([
                ('x_record_id', 'in', record_ids)
            ]).unlink()

            return True
        except Exception as e:
            _logger.error(f"Failed to permanently delete records in {model_name}: {e}")
            raise

    @api.model
    def populate_wizard_records(self, model_name, wizard_model_name):
        """
        Populate the wizard with soft-deleted records of the given model.
        Called by the 'Populate ... Records' server action.
        """
        try:
            _logger.info(f"Populating wizard {wizard_model_name} for model {model_name}")

            model = self.env[model_name]
            wizard_model = self.env[wizard_model_name]
            ir_model = self.env['ir.model'].search([('model', '=', model_name)], limit=1)

            if not ir_model:
                raise ValueError(f"Model {model_name} not found in ir.model")

            # Get soft-deleted records
            deleted_records = model.with_context(active_test=False).search([('x_is_deleted', '=', True)])

            # Clear outdated wizard records
            existing_wizards = wizard_model.search([('x_model_id', '=', ir_model.id)])
            for wiz in existing_wizards:
                if not model.browse(wiz.x_record_id).exists() or not model.browse(wiz.x_record_id).x_is_deleted:
                    wiz.unlink()

            # Create new wizard entries
            vals_list = []
            for record in deleted_records:
                if not wizard_model.search([('x_model_id', '=', ir_model.id), ('x_record_id', '=', record.id)], limit=1):
                    vals_list.append({
                        'x_model_id': ir_model.id,
                        'x_record_id': record.id,
                        'x_display_name': record.display_name or f"Record {record.id}",
                    })

            if vals_list:
                wizard_model.create(vals_list)
                _logger.info(f"Created {len(vals_list)} wizard records for {wizard_model_name}")

        except Exception as e:
            _logger.error(f"Failed to populate wizard records for {model_name}: {e}")
            raise

    @api.model
    def restore_records(self, model_name, record_ids):
        """
        Restore soft-deleted records by setting x_is_deleted = False
        """
        try:
            records = self.env[model_name].browse(record_ids)
            if not records:
                return True

            records.write({'x_is_deleted': False})
            _logger.info(f"Restored {len(records)} records in {model_name}")

            # Clean up wizard entries
            wizard_model_name = f"x_{model_name.replace('.', '_')}_wizard"
            self.env[wizard_model_name].search([
                ('x_record_id', 'in', record_ids)
            ]).unlink()

            return True
        except Exception as e:
            _logger.error(f"Failed to restore records in {model_name}: {e}")
            raise

    @api.model
    def permanent_delete_records(self, model_name, record_ids):
        """
        Permanently delete soft-deleted records using original unlink
        """
        try:
            records = self.env[model_name].browse(record_ids)
            if not records:
                return True

            # Use the original unlink if patched
            if hasattr(records, 'unlink_original'):
                records.unlink_original()
            else:
                records.unlink()

            _logger.info(f"Permanently deleted {len(records)} records in {model_name}")

            # Clean up wizard entries
            wizard_model_name = f"x_{model_name.replace('.', '_')}_wizard"
            self.env[wizard_model_name].search([
                ('x_record_id', 'in', record_ids)
            ]).unlink()

            return True
        except Exception as e:
            _logger.error(f"Failed to permanently delete records in {model_name}: {e}")
            raise

    # @api.model
    # def ensure_all_server_actions(self):
    #     """Ensure server actions exist for all configured models."""
    #     config = self._get_or_create_config()
    #     IrModel = self.env['ir.model']
    #     for model in config.model_ids:
    #         wizard_model_name = f"x_{model.model.replace('.', '_')}_wizard"
    #         if not IrModel.search([('model', '=', wizard_model_name)], limit=1):
    #             _logger.warning(f"Wizard model {wizard_model_name} does not exist, creating it")
    #             self._create_dynamic_wizard_model_and_view(model.model)
    #         self._ensure_server_action(model, wizard_model_name)
    #     _logger.info("Verified server actions for all configured models")

    def _apply_domain_to_actions(self, model_ids):
        IrModel = self.env['ir.model']
        IrModelData = self.env['ir.model.data']
        IrActionsActWindow = self.env['ir.actions.act_window']

        # raise UserError(model_ids)

        for model in IrModel.browse(model_ids):
            action = IrActionsActWindow.search([
                ('res_model', '=', model.model),
                '|','|',
                ('view_mode', 'ilike', 'tree'),
                ('view_mode', 'ilike', 'form'),
                ('view_mode', 'ilike', 'kanban'),
            ], limit=1)
            _logger.debug(f"Processing action {action.name} with view_mode {action.view_mode} for model {model.model}")

            if action:
                action.write({
                    'domain': "[('x_is_deleted', '=', False)]"
                })
                xml_id_record = IrModelData.search([
                    ('model', '=', 'ir.actions.act_window'),
                    ('res_id', '=', action.id)
                ], limit=1)
                if xml_id_record:
                    _logger.info(f"Updated domain for action {xml_id_record.module}.{xml_id_record.name} of model {model.model}")
                else:
                    _logger.info(f"Updated domain for action (no XML ID) of model {model.model}")
            else:
                _logger.warning(f"No action found for model {model.model}")

    def _apply_soft_delete(self, new_model_ids, previous_model_ids):
        return self.env['res.config.settings']._apply_soft_delete(new_model_ids, previous_model_ids)

    # @api.model
    # def get_values(self):
    #     res = super(SoftDeleteConfigSettings, self).get_values()
    #     # config = self._get_or_create_config()
    #     # self.ensure_all_server_actions()

    #     # ICPSudo = self.env['ir.config_parameter'].sudo()
    #     # ids_str = ICPSudo.get_param('soft_delete_recocords_recovery.specific_models_recover', default='')
    #     # model_ids = [int(id) for id in ids_str.split(',') if id]

    #     res.update({
    #         # 'config_id': config.id,
    #         # 'model_ids': [(6, 0, config.model_ids.ids)],
    #         # 'select_all_permanent_delete': ICPSudo.get_param('soft_delete_recocords_recovery.select_all_permanent_delete', default='True') == 'True',
    #         # 'specific_models_recover': [(6, 0, model_ids)],
    #     })
    #     return res

    # @api.model
    # def _get_or_create_config(self):
    #     config = self.env['soft.delete.manager.config'].search([], limit=1)
    #     if not config:
    #         config = self.env['soft.delete.manager.config'].create({})
    #     return config

    def _create_dynamic_wizard_model_and_view(self, model_name):
        IrModel = self.env['ir.model']
        IrModelFields = self.env['ir.model.fields']
        IrUiView = self.env['ir.ui.view']
        IrActionsServer = self.env['ir.actions.server']

        wizard_model_name = f"x_{model_name.replace('.', '_')}_wizard"
        wizard_class_name = wizard_model_name.replace('.', '_')

        existing_model = IrModel.search([('model', '=', wizard_model_name)], limit=1)
        if existing_model:
            _logger.info(f"Wizard model {wizard_model_name} already exists.")
            return wizard_model_name

        wizard_model = IrModel.create({
            'name': wizard_class_name,
            'model': wizard_model_name,
            'state': 'manual'
        })

        for field_data in [
            {
                'name': 'x_model_id',
                'field_description': 'Screen Name',
                'ttype': 'many2one',
                'relation': 'ir.model',
                'domain': "[('model', '!=', False)]",
                'readonly': True,
            },
            {
                'name': 'x_record_id',
                'field_description': 'Original Record ID',
                'ttype': 'integer',
                'readonly': True,
            },
            {
                'name': 'x_display_name',
                'field_description': 'Name',
                'ttype': 'char',
                'readonly': True,
            },
        ]:
            existing_field = IrModelFields.search([
                ('model', '=', wizard_model_name),
                ('name', '=', field_data['name'])
            ], limit=1)
            if not existing_field:
                field_data.update({
                    'model_id': wizard_model.id,
                    'model': wizard_model_name,
                    'state': 'manual',
                })
                IrModelFields.create(field_data)
                _logger.info(f"Created field '{field_data['name']}' for model: {wizard_model_name}")
            else:
                _logger.info(f"Field '{field_data['name']}' already exists for model: {wizard_model_name}")

        IrUiView.create({
            'name': f'{wizard_model_name}.form',
            'model': wizard_model_name,
            'arch': f'''
                <form string="{wizard_class_name}">
                    <sheet>
                        <group>
                            <field name="x_model_id"/>
                            <field name="x_record_id"/>
                            <field name="x_display_name"/>
                        </group>
                    </sheet>
                </form>
            ''',
            'type': 'form'
        })

        restore_action_name = f"Restore {wizard_class_name} Records"
        existing_restore_action = IrActionsServer.search([
            ('name', '=', restore_action_name),
            ('model_id.model', '=', wizard_model_name),
        ], limit=1)

        if not existing_restore_action:
            restore_action = IrActionsServer.create({
                'name': restore_action_name,
                'model_id': wizard_model.id,
                'state': 'code',
                'code': f"""
                    env['res.config.settings'].restore_records('{model_name}', records.mapped('x_record_id'))
                """,
            })
            _logger.info(f"Created restore server action '{restore_action_name}' for wizard {wizard_model_name}")
        else:
            restore_action = existing_restore_action
            _logger.info(f"Using existing restore server action '{restore_action_name}' for wizard {wizard_model_name}")

        delete_action_name = f"Permanent Delete {wizard_class_name} Records"
        existing_delete_action = IrActionsServer.search([
            ('name', '=', delete_action_name),
            ('model_id.model', '=', wizard_model_name),
        ], limit=1)

        if not existing_delete_action:
            delete_action = IrActionsServer.create({
                'name': delete_action_name,
                'model_id': wizard_model.id,
                'state': 'code',
                'code': f"""
                    env['res.config.settings'].permanent_delete_records('{model_name}', records.mapped('x_record_id'))
                """,
            })
            _logger.info(f"Created permanent delete server action '{delete_action_name}' for wizard {wizard_model_name}")
        else:
            delete_action = existing_delete_action
            _logger.info(f"Using existing permanent delete server action '{delete_action_name}' for wizard {wizard_model_name}")

        IrUiView.create({
            'name': f'{wizard_model_name}.tree',
            'model': wizard_model_name,
            'arch': f'''
                <tree string="{wizard_class_name}" create="false" edit="false" delete="false">
                    <header>
                        <button name="{restore_action.id}" string="Restore" type="action" icon="fa-undo" confirm="Are you sure you want to restore the selected records?"/>
                        <button name="{delete_action.id}" string="Permanent Delete" type="action" icon="fa-trash" confirm="Are you sure you want to permanently delete the selected records?"/>
                    </header>
                    <field name="x_model_id"/>
                    <field name="x_record_id" invisible="1"/>
                    <field name="x_display_name"/>
                </tree>
            ''',
            'type': 'tree'
        })

        _logger.info(f"Created wizard and views for model: {wizard_model_name}")
        return wizard_model_name

    def action_cleanup_soft_delete(self):
        """
        Action to clean up all models, views, and server actions starting with 'x_'.
        Removes the 'x_is_deleted' field from all models, inherited views containing
        the 'x_is_deleted' domain, and clears domains from actions.
        Only accessible by the superuser.
        """
        # self.ensure_one()
        if self.env.user.id != SUPERUSER_ID:
            raise AccessError(_("This action is restricted to the superuser only."))
        
        try:
            # Begin transaction
            # self.env.cr.execute("BEGIN;")

            # Step 1: Remove 'x_is_deleted' field from all models
            _logger.info("Starting cleanup of 'x_is_deleted' fields from all models")
            x_is_deleted_fields = self.env['ir.model.fields'].search([('name', '=', 'x_is_deleted')])
            if x_is_deleted_fields:
                model_names = [field.model for field in x_is_deleted_fields]
                _logger.info(f"Found {len(x_is_deleted_fields)} 'x_is_deleted' fields in models: {model_names}")
                x_is_deleted_fields.with_context(_force_unlink=True).unlink()
                _logger.info(f"Deleted {len(x_is_deleted_fields)} 'x_is_deleted' fields")
            else:
                _logger.info("No 'x_is_deleted' fields found in any models")

            # Step 2: Remove inherited views containing 'x_is_deleted' domain
            _logger.info("Starting cleanup of inherited views with 'x_is_deleted' domain")
            inherited_views = self.env['ir.ui.view'].search([
                ('name', 'in', [
                    'x_soft_delete_manager.tree.view.inherit.dynamic',
                    'x_soft_delete_manager.tree.view.x_is_deleted.inherit.dynamic',
                    'x_soft_delete_manager.tree.view.js_class.inherit.dynamic',
                    'x_soft_delete_manager.kanban.view.inherit.dynamic',
                    'x_soft_delete_manager.kanban.view.x_is_deleted.inherit.dynamic',
                    'x_soft_delete_manager.kanban.view.js_class.inherit.dynamic',
                ])
            ])
            if inherited_views:
                view_data = self.env['ir.model.data'].search([
                    ('model', '=', 'ir.ui.view'),
                    ('res_id', 'in', inherited_views.ids)
                ])
                if view_data:
                    view_data.with_context(_force_unlink=True).unlink()
                    _logger.info(f"Force deleted {len(view_data)} ir.model.data entries for inherited views")
                inherited_views.with_context(_force_unlink=True).unlink()
                _logger.info(f"Deleted {len(inherited_views)} inherited views with 'x_is_deleted' domain")
            else:
                _logger.info("No inherited views found with 'x_is_deleted' domain")

            # Step 3: Clear domains from ir.actions.act_window
            _logger.info("Starting cleanup of domains from ir.actions.act_window")
            actions = self.env['ir.actions.act_window'].search([
                ('domain', '=', "[('x_is_deleted', '=', False)]"),
                '|', '|',
                ('view_mode', 'ilike', 'tree'),
                ('view_mode', 'ilike', 'form'),
                ('view_mode', 'ilike', 'kanban'),
            ])
            if actions:
                for action in actions:
                    action.write({'domain': False})
                    xml_id_record = self.env['ir.model.data'].search([
                        ('model', '=', 'ir.actions.act_window'),
                        ('res_id', '=', action.id)
                    ], limit=1)
                    if xml_id_record:
                        _logger.info(f"Cleared domain for action {xml_id_record.module}.{xml_id_record.name}")
                    else:
                        _logger.info(f"Cleared domain for action (no XML ID) with ID {action.id}")
            else:
                _logger.info("No actions found with x_is_deleted domain")

            # Step 4: Remove models starting with 'x_'
            _logger.info("Starting cleanup of models starting with 'x_'")
            model_ids = self.env['ir.model'].search([('model', '=like', 'x_%'), ('model', '!=', 'soft.delete.manager.all.modules')]).ids
            if model_ids:
                models = self.env['ir.model'].browse(model_ids)
                model_names = [model.model for model in models]
                _logger.info(f"Found {len(model_names)} models to delete: {model_names}")

                # Force delete ir.model.data entries, including noupdate=1
                model_data = self.env['ir.model.data'].search([('model', '=', 'ir.model'), ('res_id', 'in', model_ids)])
                if model_data:
                    model_data.with_context(_force_unlink=True).unlink()
                    _logger.info(f"Force deleted {len(model_data)} ir.model.data entries for models")

                # Remove related fields (excluding x_is_deleted, already handled)
                fields = self.env['ir.model.fields'].search([('model_id', 'in', model_ids), ('name', '!=', 'x_is_deleted')])
                if fields:
                    fields.with_context(_force_unlink=True).unlink()
                    _logger.info(f"Deleted {len(fields)} fields for models")

                # Drop database tables for these models
                for model_name in model_names:
                    table_name = model_name.replace('.', '_')
                    try:
                        self.env.cr.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE;")
                        _logger.info(f"Dropped table {table_name}")
                    except Exception as e:
                        _logger.warning(f"Failed to drop table {table_name}: {str(e)}")

                # Delete the models
                models.with_context(_force_unlink=True).unlink()
                _logger.info(f"Deleted {len(model_ids)} models starting with 'x_'")

            # Step 5: Remove views associated with models starting with 'x_'
            _logger.info("Starting cleanup of views for models starting with 'x_'")
            view_ids = self.env['ir.ui.view'].search([('model', '=like', 'x_%')]).ids
            if view_ids:
                views = self.env['ir.ui.view'].browse(view_ids)
                _logger.info(f"Found {len(view_ids)} views to delete for models starting with 'x_'")
                
                # Force delete ir.model.data entries for views
                view_data = self.env['ir.model.data'].search([('model', '=', 'ir.ui.view'), ('res_id', 'in', view_ids)])
                if view_data:
                    view_data.with_context(_force_unlink=True).unlink()
                    _logger.info(f"Force deleted {len(view_data)} ir.model.data entries for views")
                
                # Delete the views
                views.with_context(_force_unlink=True).unlink()
                _logger.info(f"Deleted {len(view_ids)} views for models starting with 'x_'")
            else:
                _logger.info("No views found for models starting with 'x_'")

            # Step 6: Remove server actions related to models starting with 'x_'
            _logger.info("Starting cleanup of server actions for models starting with 'x_'")
            action_ids = self.env['ir.actions.server'].search([('model_id.model', '=like', 'x_%')]).ids
            if action_ids:
                actions = self.env['ir.actions.server'].browse(action_ids)
                _logger.info(f"Found {len(action_ids)} server actions to delete for models starting with 'x_'")
                
                # Force delete ir.model.data entries for actions
                action_data = self.env['ir.model.data'].search([('model', '=', 'ir.actions.server'), ('res_id', 'in', action_ids)])
                if action_data:
                    action_data.with_context(_force_unlink=True).unlink()
                    _logger.info(f"Force deleted {len(action_data)} ir.model.data entries for server actions")
                
                # Delete the actions
                actions.with_context(_force_unlink=True).unlink()
                _logger.info(f"Deleted {len(action_ids)} server actions for models starting with 'x_'")

            # Step 7: Clean up soft delete configuration
            _logger.info("Cleaning up soft delete configuration")
            config = self.env['res.config.settings'].search([], limit=1)
            if config:
                config_data = self.env['ir.model.data'].search([('model', '=', 'res.config.settings'), ('res_id', '=', config.id)])
                if config_data:
                    config_data.with_context(_force_unlink=True).unlink()
                    _logger.info(f"Force deleted ir.model.data entries for res.config.settings")
                config.unlink()
                _logger.info("Deleted res.config.settings record")

            # Commit the transaction
            # self.env.cr.execute("COMMIT;")
            _logger.info("Cleanup action completed successfully")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('All models, views, server actions starting with "x_", the "x_is_deleted" field, and related domains have been deleted.'),
                    'type': 'success',
                    'sticky': False,
                }
            }

        except Exception as e:
            # Rollback on error
            # self.env.cr.execute("ROLLBACK;")
            _logger.error(f"Error during cleanup action: {str(e)}")
            raise

    @api.onchange('specific_models_recover')
    def _onchange_specific_models_recover(self):
        if self.specific_models_recover:
            self.select_all_permanent_delete = False
        else:
            self.select_all_permanent_delete = True
