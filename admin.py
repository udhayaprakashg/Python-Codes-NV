# scheduler_app/admin.py
from django.contrib import admin, messages
from django import forms
from django.utils.html import format_html
from django_q.tasks import async_task
from django.conf import settings
from pathlib import Path
import zipfile, shutil, tempfile

from .models import (
    VirtualEnv,
    Deployment,
    DeploymentVersion,
    ScheduledJob,
    JobLog,
)


@admin.register(VirtualEnv)
class VirtualEnvAdmin(admin.ModelAdmin):
    list_display = ("name", "path", "created_at")
    search_fields = ("name",)


class DeploymentVersionInline(admin.TabularInline):
    model = DeploymentVersion
    readonly_fields = ("version_number", "created_at", "extracted_path")
    fields = ("zip_file", "virtual_env", "version_number", "created_at")
    extra = 0
    can_delete = False

    def get_formset(self, request, obj=None, **kwargs):
        fs = super().get_formset(request, obj, **kwargs)
        fs.form.base_fields["virtual_env"].required = True
        fs.form.base_fields["virtual_env"].empty_label = None
        return fs


@admin.register(Deployment)
class DeploymentAdmin(admin.ModelAdmin):
    list_display = ("custom_name", "unique_id_short", "created_at")
    search_fields = ("custom_name",)
    inlines = [DeploymentVersionInline]

    def unique_id_short(self, obj):
        return obj.unique_id.hex[:8]

    unique_id_short.short_description = "ID"

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        for fs in formsets:
            if fs.model is DeploymentVersion:
                for obj in fs.new_objects:
                    self._extract_zip(obj, request)

    def _extract_zip(self, version_obj, request):
        zip_path = version_obj.zip_file.path
        dep = version_obj.deployment
        base = Path(settings.DEPLOYMENT_FOLDER) / str(dep.id) / str(version_obj.id)
        base.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(tmp)
            for item in Path(tmp).iterdir():
                shutil.move(str(item), base / item.name)

        version_obj.extracted_path = str(base)
        version_obj.save(update_fields=["extracted_path"])
        messages.success(
            request,
            f"v{version_obj.version_number} extracted using {version_obj.virtual_env.name}",
        )


# scheduler_app/admin.py
class ScheduledJobAdminForm(forms.ModelForm):
    class Meta:
        model = ScheduledJob
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.deployment_version:
            dep = self.instance.deployment_version.deployment
            self.fields["deployment_version"].queryset = DeploymentVersion.objects.filter(
                deployment=dep
            )
        else:
            # Optional: limit to versions with virtual_env
            self.fields["deployment_version"].queryset = DeploymentVersion.objects.all()


class JobLogInline(admin.TabularInline):
    model = JobLog
    def log_id(self, obj):
        return obj.id

    log_id.short_description = "ID"
    show_change_link = True
    readonly_fields = (
        "log_id",
        "started_at",
        "finished_at",
        "success",
        "deployment_version",
        "message",
    )
    extra = 0
    can_delete = False
    ordering = ("-started_at",)          # NEW: newest first
    fields = (
        "log_id",
        "started_at",
        "finished_at",
        "success",
        "deployment_version",
        "message",
    )

@admin.register(ScheduledJob)
class ScheduledJobAdmin(admin.ModelAdmin):
    form = ScheduledJobAdminForm
    list_display = (
        "name",
        "deployment_link",
        "trigger_type",
        "enabled",
        "last_run",
        "next_run",
        "last_status",
        
    )
    list_filter = ("trigger_type", "enabled")
    search_fields = ("name",)
    readonly_fields = (
        "q_schedule_id",
        "created_at",
        "updated_at",
        "last_run",
        "next_run",
        "last_status",
        
    )
    inlines = [JobLogInline]
    actions = ["run_now"]

   # === ADD THIS METHOD (below deployment_link) ===
    def next_run(self, obj):
        value = obj.next_run()
        if value == "watching folder":
            return format_html('<span style="color:#28a745;">watching folder</span>')
        if value == "—":
            return "—"

        # Format: Nov. 16, 2025, 10:12 a.m.
        day = str(value.day)  # Remove leading zero
        month = value.strftime("%b").title()  # Nov, Dec, etc.
        formatted = f"{month}. {day}, {value:%Y}, {value:%I:%M %p}"
        formatted = formatted.replace("AM", "a.m.").replace("PM", "p.m.")

        return format_html(
            '<span title="{}">{}</span>',
            value.isoformat(),
            formatted
        )
    next_run.short_description = "Next run"

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "deployment_version__deployment", "deployment_version__virtual_env"
            )
        )

    def deployment_link(self, obj):
        if not obj.deployment_version:
            return "-"
        dep = obj.deployment_version.deployment
        venv = obj.deployment_version.virtual_env
        return format_html(
            '<a href="/admin/scheduler_app/deployment/{0}/change/">{1}</a> '
            '<code>v{2}</code> <small>{3}</small>',
            dep.id,
            dep.custom_name,
            obj.deployment_version.version_number,
            venv.name,
        )

    deployment_link.short_description = "Deployment"

    def run_now(self, request, queryset):
        for job in queryset.filter(enabled=True):
            if not job.deployment_version:
                self.message_user(
                    request, f"Job '{job}' has no version.", level=messages.WARNING
                )
                continue
            async_task(
                "scheduler_app.tasks.execute_job",
                job.id,
                #using=job.get_db_alias(), #skipped
            )
        self.message_user(request, "Jobs triggered.", level=messages.SUCCESS)

    run_now.short_description = "Run now"        



@admin.register(JobLog)
class JobLogAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "version_display", "started_at", "success")
    readonly_fields = (
        "id",
        "job",
        "deployment_version",
        "started_at",
        "finished_at",
        "success",
        "message",
    )

    def version_display(self, obj):
        if obj.deployment_version:
            return f"v{obj.deployment_version.version_number} ({obj.deployment_version.virtual_env.name})"
        return "-"

    # === DISABLE ADD, DELETE, AND CHANGE PERMISSIONS ===
    def has_add_permission(self, request):
        return False  # No "Add JobLog" button

    def has_change_permission(self, request, obj=None):
        return False  # No editing

    #def has_delete_permission(self, request, obj=None):
    #    return False  # No deleting
    version_display.short_description = "Version"
