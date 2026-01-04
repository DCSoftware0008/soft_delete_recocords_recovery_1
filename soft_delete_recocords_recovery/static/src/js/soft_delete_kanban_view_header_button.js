/** @odoo-module */
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { registry } from "@web/core/registry";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { useService } from "@web/core/utils/hooks";

export class SoftDeleteManagerKanbanController extends KanbanController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");

        console.info("SoftDeleteManagerKanbanController initialized", {
            model: this.props.resModel,
        });
    }

    async onRecoverClick() {
        console.log("Recover button clicked in Kanban");
        const modelName = this.props.resModel;
        const wizardModelName = `x_${modelName.replace(/\./g, '_')}_wizard`;

        console.info("Preparing to populate wizard records", {
            modelName,
            wizardModelName,
        });

        try {
            // Find the server action
            const serverActions = await this.orm.searchRead(
                'ir.actions.server',
                [['name', '=', `Populate ${wizardModelName} Records`]],
                ['id'],
                { limit: 1 }
            );

            if (!serverActions.length) {
                console.error("Server action not found", {
                    actionName: `Populate ${wizardModelName} Records`,
                    wizardModelName,
                    modelName,
                });
                this.notification.add(
                    `Server action 'Populate ${wizardModelName} Records' not found. Please ensure the model '${modelName}' is configured in Soft Delete Manager settings.`,
                    { type: "danger", sticky: true }
                );
                return;
            }

            const serverActionId = serverActions[0].id;
            console.info("Found server action", { serverActionId, wizardModelName });

            // Execute the server action
            await this.orm.call('ir.actions.server', 'run', [serverActionId]);
            console.info("Wizard records populated successfully", { wizardModelName });

            // Nice display name: cargo.short.name.master → Cargo Short Name Master
            const displayModelName = modelName
                .split('.')
                .map(word => word.charAt(0).toUpperCase() + word.slice(1))
                .join(' ');

            // Open the recovery wizard
            await this.actionService.doAction({
                type: 'ir.actions.act_window',
                name: `${displayModelName} Recover Deleted Records`,
                res_model: wizardModelName,
                view_mode: 'kanban',
                views: [[false, 'kanban']],
                target: 'current',
                domain: [['x_model_id.model', '=', modelName]],
            });

            console.log("Recovery wizard opened", {
                wizardModelName,
                title: `${displayModelName} Recover Deleted Records`
            });
        } catch (err) {
            console.error("Error in onRecoverClick (Kanban)", {
                error: err.message || err,
                modelName,
                wizardModelName,
            });
            this.notification.add(
                `Error: ${err.message || "Could not recover records"}`,
                { type: "danger", sticky: true }
            );
        }
    }
}

// Register a custom Kanban view (same pattern as your list view)
registry.category("views").add('soft_delete_manager_kanban', {
    ...kanbanView,
    Controller: SoftDeleteManagerKanbanController,
    buttonTemplate: "soft_delete_manager.KanbanViewButtons",
});