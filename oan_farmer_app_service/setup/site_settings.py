"""Settings the oan-farmer-app-service chart writes into the DB on every install and upgrade.

The chart's site-settings Job runs `bench execute oan_farmer_app_service.setup.site_settings.sync` with the
values in an environment variable, as JSON. Nothing is written to site_config.json.
"""

import json
import os

import frappe
from frappe import _

IAM_SERVICE_DOCTYPE = "OAN IAM Service"
IAM_SERVICES_ENV = "OAN_IAM_SERVICES"


def sync():
	"""Create or update the `OAN IAM Service` rows given as a JSON list in $OAN_IAM_SERVICES.

	Each entry is matched by `key` and uses the DocType's field names; the fields it names are set and
	the others are left as they are. `default_roles` is a list of role names. Rows that are not listed
	are kept.
	"""
	entries = json.loads(os.environ.get(IAM_SERVICES_ENV) or "[]")
	meta = frappe.get_meta(IAM_SERVICE_DOCTYPE)
	for entry in entries:
		key = entry.get("key")
		if not key:
			frappe.throw(_("Every entry of {0} needs a `key`.").format(IAM_SERVICES_ENV))
		doc = (
			frappe.get_doc(IAM_SERVICE_DOCTYPE, key)
			if frappe.db.exists(IAM_SERVICE_DOCTYPE, key)
			else frappe.new_doc(IAM_SERVICE_DOCTYPE)
		)
		for fieldname, value in entry.items():
			if fieldname == "default_roles":
				doc.set("default_roles", [{"role": role} for role in value or []])
			elif meta.has_field(fieldname):
				doc.set(fieldname, value)
			else:
				frappe.throw(
					_("{0}[{1}]: {2} has no field `{3}`.").format(
						IAM_SERVICES_ENV, key, IAM_SERVICE_DOCTYPE, fieldname
					)
				)
		doc.save(ignore_permissions=True)
	print(f"{IAM_SERVICE_DOCTYPE}: {len(entries)} row(s) written")
