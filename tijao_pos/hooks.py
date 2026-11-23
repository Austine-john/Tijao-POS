app_name = "tijao_pos"
app_title = "Tijao POS"
app_publisher = "Tijao"
app_description = "Point of sale and inventory for a small soda shop"
app_email = "dev@tijao.local"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "tijao_pos",
# 		"logo": "/assets/tijao_pos/logo.png",
# 		"title": "Tijao POS",
# 		"route": "/tijao_pos",
# 		"has_permission": "tijao_pos.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/tijao_pos/css/tijao_pos.css"
# app_include_js = "/assets/tijao_pos/js/tijao_pos.js"

# include js, css files in header of web template
# web_include_css = "/assets/tijao_pos/css/tijao_pos.css"
# web_include_js = "/assets/tijao_pos/js/tijao_pos.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "tijao_pos/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "tijao_pos/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "tijao_pos.utils.jinja_methods",
# 	"filters": "tijao_pos.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "tijao_pos.install.before_install"
# after_install = "tijao_pos.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "tijao_pos.uninstall.before_uninstall"
# after_uninstall = "tijao_pos.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "tijao_pos.utils.before_app_install"
# after_app_install = "tijao_pos.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "tijao_pos.utils.before_app_uninstall"
# after_app_uninstall = "tijao_pos.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "tijao_pos.build.after_build"

# To hook into the build process of other apps
# The list of apps being built is passed as an argument

# after_app_build = "tijao_pos.build.after_app_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "tijao_pos.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["tijao_pos.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"tijao_pos.tasks.all"
# 	],
# 	"daily": [
# 		"tijao_pos.tasks.daily"
# 	],
# 	"hourly": [
# 		"tijao_pos.tasks.hourly"
# 	],
# 	"weekly": [
# 		"tijao_pos.tasks.weekly"
# 	],
# 	"monthly": [
# 		"tijao_pos.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "tijao_pos.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "tijao_pos.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "tijao_pos.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["tijao_pos.utils.before_request"]
# after_request = ["tijao_pos.utils.after_request"]

# Job Events
# ----------
# before_job = ["tijao_pos.utils.before_job"]
# after_job = ["tijao_pos.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"tijao_pos.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []


# ---------------------------------------------------------------------------
# Tijao POS
# ---------------------------------------------------------------------------

# Roles + default settings are created right after `bench install-app`.
after_install = "tijao_pos.setup.install.after_install"
after_migrate = "tijao_pos.setup.install.after_migrate"

# Custom fields we add to ERPNext doctypes (never edit core).
fixtures = [
	{
		"dt": "Custom Field",
		"filters": [["module", "=", "Tijao POS"]],
	},
]

# React POS: /pos and every client-side route under it render www/pos.html.
website_route_rules = [
	{"from_route": "/pos/<path:app_path>", "to_route": "pos"},
]
