app_name = "oan_farmer_app_service"
app_title = "OAN Farmer App Service"
app_publisher = "OpenAgriNet"
app_description = "Mobile backend-for-frontend for the OAN farmer app"
app_email = "admin@openagrinet.org"
app_license = "mit"

required_apps = ["oan_auth_service"]

after_install = "oan_farmer_app_service.setup.install.after_install"
after_migrate = "oan_farmer_app_service.setup.install.after_migrate"

before_request = ["oan_farmer_app_service.api.router.ensure_routes_registered"]
