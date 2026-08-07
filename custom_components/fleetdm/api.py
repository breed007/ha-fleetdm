"""Thin async client for the Fleet REST API.

Only the read-only endpoints needed by the integration are implemented. Every
call goes through :meth:`FleetClient._get`, which normalises Fleet's error
responses into the exception types below so the coordinator can tell the
difference between "your token is bad" (reauth) and "your token is fine but
this role can't read that" (degrade gracefully).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_PREFIX = "/api/latest/fleet"
DEFAULT_TIMEOUT = 15

# Hard cap on pagination loops, so a misbehaving or very large server can never
# spin us forever.
MAX_PAGES = 20

# Fleet's default page size is not documented for every list endpoint and has
# differed between releases, so paginate explicitly rather than trusting it.
POLICIES_PER_PAGE = 100


class FleetError(Exception):
    """Base error for all Fleet API failures."""


class FleetConnectionError(FleetError):
    """The Fleet server could not be reached."""


class FleetAuthError(FleetError):
    """The API token was rejected (HTTP 401). Triggers reauth."""


class FleetForbiddenError(FleetError):
    """The token is valid but lacks permission for this endpoint (HTTP 403).

    This is deliberately distinct from :class:`FleetAuthError`: re-prompting for
    a token will not fix a role permission problem, and a least-privilege
    Observer token is an explicitly supported configuration.
    """


def normalize_url(url: str) -> str:
    """Normalise a Fleet base URL for use as a config entry unique ID.

    Strips trailing slashes and whitespace and defaults to HTTPS when no scheme
    was given, so that ``fleet.example.com``, ``https://fleet.example.com`` and
    ``https://fleet.example.com/`` all resolve to the same entry.
    """
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        raise ValueError("URL must not be empty")
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    return cleaned


@dataclass(frozen=True, slots=True)
class FleetPolicy:
    """A Fleet global policy and its current host counts."""

    id: int
    name: str
    description: str
    resolution: str
    platform: str
    critical: bool
    passing_host_count: int
    failing_host_count: int
    host_count_updated_at: str | None

    @property
    def is_failing(self) -> bool:
        """Whether any host currently fails this policy."""
        return self.failing_host_count > 0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FleetPolicy:
        """Build a policy from a Fleet API payload, tolerating absent fields.

        ``critical`` is a Fleet Premium field; on Free it may be absent
        entirely, so it defaults to False rather than raising.
        """
        return cls(
            id=int(data["id"]),
            name=str(data.get("name") or f"Policy {data['id']}"),
            description=str(data.get("description") or ""),
            resolution=str(data.get("resolution") or ""),
            platform=str(data.get("platform") or ""),
            critical=bool(data.get("critical", False)),
            passing_host_count=int(data.get("passing_host_count") or 0),
            failing_host_count=int(data.get("failing_host_count") or 0),
            host_count_updated_at=data.get("host_count_updated_at"),
        )


@dataclass(frozen=True, slots=True)
class FleetHostSummary:
    """Fleet-wide host counts by status."""

    total: int
    online: int
    offline: int
    missing: int
    new: int

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FleetHostSummary:
        """Build a summary from the ``/host_summary`` payload.

        Fleet renamed the >30-day bucket from ``mia_count`` to
        ``missing_30_days_count``; both are still emitted by current servers, so
        prefer the new name and fall back to the legacy one.
        """
        missing = data.get("missing_30_days_count")
        if missing is None:
            missing = data.get("mia_count") or 0
        return cls(
            total=int(data.get("totals_hosts_count") or 0),
            online=int(data.get("online_count") or 0),
            offline=int(data.get("offline_count") or 0),
            missing=int(missing),
            new=int(data.get("new_count") or 0),
        )


class FleetClient:
    """Minimal read-only Fleet API client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str,
    ) -> None:
        """Initialise the client.

        SSL verification is a property of the shared session (obtained via
        ``async_get_clientsession(hass, verify_ssl=...)``), not of the request,
        so it is not handled here.
        """
        self._session = session
        self._base_url = normalize_url(base_url)
        self._token = token

    @property
    def base_url(self) -> str:
        """The normalised Fleet base URL."""
        return self._base_url

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform an authenticated GET and return the decoded JSON body."""
        url = f"{self._base_url}{API_PREFIX}{path}"
        try:
            async with self._session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise FleetAuthError(f"Fleet rejected the API token for {path}")
                # Fleet answers 402 Payment Required for Premium-gated routes.
                if response.status in (402, 403):
                    raise FleetForbiddenError(
                        f"Fleet returned {response.status} for {path}; the token's "
                        "role or licence tier does not permit this request"
                    )
                response.raise_for_status()
                return await response.json()
        except (FleetAuthError, FleetForbiddenError):
            raise
        except aiohttp.ClientResponseError as err:
            raise FleetError(f"Fleet returned HTTP {err.status} for {path}") from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise FleetConnectionError(f"Could not reach Fleet at {url}") from err
        except ValueError as err:
            # Raised by response.json() when the body is not valid JSON, which
            # in practice means we hit something that is not a Fleet server.
            raise FleetError(f"Fleet returned a non-JSON response for {path}") from err

    async def async_get_version(self) -> dict[str, Any]:
        """Return Fleet version and build info.

        Used both to validate credentials during config flow and to populate the
        hub device's software version.
        """
        return await self._get("/version")

    async def async_is_premium(self) -> bool:
        """Return whether the server is licensed for Fleet Premium.

        Falls back to ``False`` when the token's role cannot read the config
        endpoint, so an Observer token degrades to Free-tier behaviour rather
        than failing setup.
        """
        try:
            config = await self._get("/config")
        except (FleetForbiddenError, FleetError):
            _LOGGER.info(
                "Could not read Fleet licence tier; assuming Free tier and "
                "hiding Premium-only entities"
            )
            return False
        tier = (config.get("license") or {}).get("tier")
        return str(tier).lower() == "premium"

    async def async_get_host_summary(self) -> FleetHostSummary:
        """Return fleet-wide host counts.

        One request covers online/offline/missing/new/total, which is why this
        is preferred over four separate ``/hosts/count?status=`` calls.
        """
        return FleetHostSummary.from_json(await self._get("/host_summary"))

    async def async_get_global_policies(self) -> list[FleetPolicy]:
        """Return all global policies with their pass/fail host counts.

        Paginated explicitly so a large policy library is never silently
        truncated by a server-side default page size.
        """
        policies: list[FleetPolicy] = []

        for page in range(MAX_PAGES):
            data = await self._get(
                "/global/policies",
                {"page": page, "per_page": POLICIES_PER_PAGE},
            )
            batch = data.get("policies") or []
            policies.extend(FleetPolicy.from_json(policy) for policy in batch)
            if len(batch) < POLICIES_PER_PAGE:
                break
        else:
            _LOGGER.warning(
                "Stopped reading global policies at the %d page safety cap "
                "(%d policies read). Some policies may be missing from Home "
                "Assistant; please open an issue if you genuinely have this many",
                MAX_PAGES,
                len(policies),
            )

        return policies
