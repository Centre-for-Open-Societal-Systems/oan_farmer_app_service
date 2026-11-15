"""Roles this app needs on every site, created on install and on every migrate."""

import frappe

# Given to whoever signs in with a national ID (eSignet): the `beneficiary` `OAN IAM Service`
# row names it as its default role (helm/oan-farmer-app-service/values.yaml). No Desk.
FARMER_ROLE = "OAN Farmer"

ROLES = [(FARMER_ROLE, 0)]


def after_install():
	ensure_roles()


def after_migrate():
	ensure_roles()


def ensure_roles():
	for role, desk_access in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": desk_access}).insert(
				ignore_permissions=True
			)
