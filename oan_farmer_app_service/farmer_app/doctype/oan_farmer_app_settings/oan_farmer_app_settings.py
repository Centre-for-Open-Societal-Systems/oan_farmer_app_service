import re

import frappe
from frappe import _
from frappe.model.document import Document
from oan_auth_service.api import iam_tokens

CONFIG_KEY = "oan_farmer_app_service"


def version_tuple(version: str | None) -> tuple[int, ...] | None:
	"""`1.4` as `(1, 4, 0, 0)`, padded so versions of different lengths compare. None unless dotted numbers."""
	if not version or not re.fullmatch(r"\d+(\.\d+)*", version.strip()):
		return None
	parts = [int(part) for part in version.strip().split(".")]
	return tuple(parts + [0] * (4 - len(parts)))


class OANFarmerAppSettings(Document):
	"""Operational settings of the gateway, edited in the desk by an admin."""

	def backend(self, name: str) -> dict:
		"""The settings of backend `name`: its row, with the shared timeouts filled in.

		`base_url` is per environment, so `site_config.json` may override it:
		`{"oan_farmer_app_service": {"upstreams": {"a2c": {"base_url": "..."}}}}`.
		A backend with neither a row nor an override comes back with no `base_url`.
		"""
		row = next((row for row in self.upstreams if row.upstream == name), None)
		entry = {}
		if row:
			entry = {
				"base_url": row.base_url,
				"issuer": row.issuer,
				"audience": row.audience,
				"connect_timeout": row.connect_timeout,
				"read_timeout": row.read_timeout,
			}
			entry = {key: value for key, value in entry.items() if value}

		override = ((frappe.conf.get(CONFIG_KEY) or {}).get("upstreams") or {}).get(name) or {}
		if override.get("base_url"):
			entry["base_url"] = override["base_url"]

		entry.setdefault("connect_timeout", self.connect_timeout)
		entry.setdefault("read_timeout", self.read_timeout)
		return entry

	def validate(self):
		if self.min_app_version and version_tuple(self.min_app_version) is None:
			frappe.throw(_("Minimum App Version must be dotted numbers, such as 1.4.0."))

		seen = set()
		for row in self.upstreams:
			if row.upstream in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, row.upstream))
			seen.add(row.upstream)
			base_url = (row.base_url or "").strip().rstrip("/")
			# A site_config.json override may stand in for the URL, so empty is allowed.
			if base_url and not iam_tokens.is_secure_url(base_url):
				frappe.throw(
					_("Row {0}: Base URL must be https, or http to a loopback host.").format(row.idx)
				)
			row.base_url = base_url
