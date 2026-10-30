import unittest

from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from app.main import app


class RouteAuthenticationTests(unittest.TestCase):
    def test_every_business_route_requires_authentication_except_login_and_health(self):
        with TestClient(app) as client:
            for route in app.routes:
                if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
                    continue
                path = route.path
                for name in route.param_convertors:
                    path = path.replace("{" + name + "}", "missing")
                for method in route.methods:
                    with self.subTest(method=method, path=route.path):
                        response = client.request(method, path)
                        if route.path == "/api/health":
                            self.assertEqual(response.status_code, 200)
                        elif route.path == "/api/v1/auth/login":
                            self.assertEqual(response.status_code, 422)
                        else:
                            self.assertIn(response.status_code, (401, 403))
