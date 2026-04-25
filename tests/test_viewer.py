"""Tests for the Big Brother viewer (Stage 3)."""
import os


from kbz.main import app
from agents.simulation_api import router as sim_router
from tests.conftest import create_test_user, create_test_community

# Mount simulation router for testing (idempotent check)
_sim_mounted = False
if not _sim_mounted:
    app.include_router(sim_router)
    _sim_mounted = True


class TestViewerStaticFiles:
    """Test that viewer static files exist and are properly structured."""

    def test_viewer_directory_exists(self):
        viewer_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "viewer")
        assert os.path.isdir(viewer_dir)

    def test_index_html_exists(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "viewer", "index.html")
        assert os.path.isfile(path)

    def test_style_css_exists(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "viewer", "style.css")
        assert os.path.isfile(path)

    def test_app_js_exists(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "viewer", "app.js")
        assert os.path.isfile(path)

    def test_index_html_has_react(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "viewer", "index.html")
        content = open(path).read()
        assert "react" in content.lower()
        assert "root" in content
        assert "app.js" in content

    def test_app_js_has_components(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "viewer", "app.js")
        content = open(path).read()
        assert "function App()" in content
        assert "DashboardTab" in content
        assert "AgentsTab" in content
        assert "InterviewTab" in content
        assert "TimelineTab" in content
        assert "TraitsRadarChart" in content


class TestCORSMiddleware:
    """Test that CORS middleware is configured."""

    async def test_cors_headers(self, client):
        resp = await client.options(
            "/health",
            headers={
                "origin": "http://localhost:3000",
                "access-control-request-method": "GET",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers


class TestSimulationAPI:
    """Test simulation API endpoints return proper structure."""

    async def test_simulation_status_no_simulation(self, client):
        """When no simulation is running, should return 503."""
        resp = await client.get("/simulation/status")
        assert resp.status_code == 503

    async def test_simulation_agents_no_simulation(self, client):
        resp = await client.get("/simulation/agents")
        assert resp.status_code == 503

    async def test_simulation_events_no_simulation(self, client):
        resp = await client.get("/simulation/events")
        assert resp.status_code == 503


class TestAPIEndpointsForViewer:
    """Test that the KBZ API endpoints used by the viewer return correct data."""

    async def test_proposals_list(self, client):
        user = await create_test_user(client)
        community = await create_test_community(client, user["id"])
        resp = await client.get(f"/communities/{community['id']}/proposals")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_pulses_list(self, client):
        user = await create_test_user(client)
        community = await create_test_community(client, user["id"])
        resp = await client.get(f"/communities/{community['id']}/pulses")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1  # Initial pulse created with community

    async def test_pulse_has_viewer_fields(self, client):
        user = await create_test_user(client)
        community = await create_test_community(client, user["id"])
        resp = await client.get(f"/communities/{community['id']}/pulses")
        pulse = resp.json()[0]
        assert "id" in pulse
        assert "status" in pulse
        assert "support_count" in pulse
        assert "threshold" in pulse
        assert "created_at" in pulse

    async def test_community_has_viewer_fields(self, client):
        user = await create_test_user(client)
        community = await create_test_community(client, user["id"])
        resp = await client.get(f"/communities/{community['id']}")
        data = resp.json()
        assert "id" in data
        assert "name" in data
        assert "member_count" in data
