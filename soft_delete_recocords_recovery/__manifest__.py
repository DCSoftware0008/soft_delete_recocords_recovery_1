{
    'name': 'Soft Delete Manager',
    'version': '16.0.4.0.0',
    'summary': '[V00001]Manage soft delete functionality for Odoo models',
    'description': '''
        This module allows administrators to configure soft delete functionality
        for selected Odoo models. Features include:
        - Enabling soft delete for specific models.
        - Adding a "Recover Deleted" button on tree views.
        - A wizard to recover or permanently delete records.
        For more details, see the README file.
    ''',
    'category': 'Tools',
    'author': 'DC Software',
    'website': 'https://github.com',
    'license': 'LGPL-3',
    'depends': ['base', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/soft_delete_config_settings_views.xml',
        'views/base_module_uninstall_views_inherit.xml',
        'views/soft_delete_manager_all_modules_view.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'soft_delete_recocords_recovery/static/src/js/soft_delete_kanban_view_header_button.js',
            'soft_delete_recocords_recovery/static/src/js/soft_delete_tree_view_header_button.js',
            'soft_delete_recocords_recovery/static/src/xml/soft_delete_kanban_view_header_button.xml',
            'soft_delete_recocords_recovery/static/src/xml/soft_delete_tree_view_header_button.xml',
        ],
    },
    'images': [
        'static/description/1model_name_find.png',
        'static/description/2model_name_copy_past.png',
        'static/description/3normal_user_not_access_this_button.png',
        'static/description/4become_superuser.png',
        'static/description/5deleted_records_tree_view.png',
        'static/description/6record_delete.png',
        'static/description/7deleted_records_show_in_this_tree_view.png',
        'static/description/8restore_and_permanent_delete_buttnes.png',
        'static/description/9restore_button.png',
        'static/description/10restore_records_in_main_screen.png',
        'static/description/11permanent_delete_button.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'uninstall_hook': 'uninstall_hook',
    'price': 49.99,
    'currency': 'USD',
}




