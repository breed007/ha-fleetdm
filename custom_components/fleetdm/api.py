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
from datetime import UTC, datetime
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
HOSTS_PER_PAGE = 100

# How many vulnerable software titles to keep for the sensor's attributes.
# Fleet can order server-side by affected host count, so this is the genuinely
# worst N rather than the first N that happened to be returned.
VULNERABLE_TITLES_SAMPLE = 10

# Activities come back newest-first, so a modest page is enough to catch up
# between polls; the watermark stops us reading further back than needed.
ACTIVITIES_PER_PAGE = 100

# Fleet writes this zero value for timestamps that are simply unset.
_ZERO_TIME_YEAR = 1970


def parse_fleet_time(value: Any) -> datetime | None:
    """Parse a Fleet timestamp, treating unset and unparseable values as None.

    Fleet writes ``0001-01-01T00:00:00Z`` rather than null for timestamps it has
    no value for, which would otherwise surface as a date in the year 1.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return None if parsed.year < _ZERO_TIME_YEAR else parsed


# Fleet renamed the global policies route as part of dropping "global" from its
# team terminology. Current servers answer /policies under /api/latest and 404
# on /global/policies; older servers are the other way round. Probing in this
# order and caching the winner keeps both working.
POLICY_PATHS = ("/policies", "/global/policies")


class FleetError(Exception):
    """Base error for all Fleet API failures."""


class FleetConnectionError(FleetError):
    """The Fleet server could not be reached."""


class FleetAuthError(FleetError):
    """The API token was rejected (HTTP 401). Triggers reauth."""


class FleetNotFoundError(FleetError):
    """The endpoint does not exist on this Fleet server (HTTP 404).

    Used to detect which of several known route spellings a given Fleet
    version supports, rather than to signal a hard failure.
    """


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


@dataclass(frozen=True, slots=True)
class FleetHost:
    """A single enrolled host, as returned by the host list endpoint.

    Built from ``/hosts`` rather than ``/hosts/{id}`` on purpose: the list
    already carries everything the per-host entities need, including
    ``issues.failing_policies_count``, so a fleet of any size costs one
    paginated read instead of a detail request per host.
    """

    id: int
    display_name: str
    hostname: str
    platform: str
    os_version: str
    status: str
    primary_ip: str
    hardware_model: str
    osquery_version: str
    failing_policies_count: int
    seen_time: datetime | None
    last_restarted_at: datetime | None
    disk_gigs_available: float | None
    disk_percent_available: int | None

    @property
    def is_online(self) -> bool:
        """Whether Fleet currently considers this host online."""
        return self.status == "online"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FleetHost:
        """Build a host from a Fleet API payload, tolerating absent fields."""
        host_id = int(data["id"])
        # Fleet's own display preference, falling back the way its UI does.
        display = (
            data.get("display_name")
            or data.get("computer_name")
            or data.get("hostname")
            or f"Host {host_id}"
        )
        issues = data.get("issues") or {}
        return cls(
            id=host_id,
            display_name=str(display),
            hostname=str(data.get("hostname") or ""),
            platform=str(data.get("platform") or ""),
            os_version=str(data.get("os_version") or ""),
            status=str(data.get("status") or ""),
            primary_ip=str(data.get("primary_ip") or ""),
            hardware_model=str(data.get("hardware_model") or ""),
            osquery_version=str(data.get("osquery_version") or ""),
            failing_policies_count=int(issues.get("failing_policies_count") or 0),
            seen_time=parse_fleet_time(data.get("seen_time")),
            # Fleet reports uptime as a duration, but also gives the boot time
            # directly. The timestamp is what a Home Assistant sensor wants.
            last_restarted_at=parse_fleet_time(data.get("last_restarted_at")),
            disk_gigs_available=_opt_float(data.get("gigs_disk_space_available")),
            disk_percent_available=_opt_int(data.get("percent_disk_space_available")),
        )


@dataclass(frozen=True, slots=True)
class FleetSoftwareTitle:
    """A software title with known vulnerabilities."""

    id: int
    name: str
    source: str
    hosts_count: int
    cve_count: int

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FleetSoftwareTitle:
        """Build a title, counting CVEs across all of its versions."""
        cves: set[str] = set()
        for version in data.get("versions") or []:
            for cve in version.get("vulnerabilities") or []:
                if isinstance(cve, str):
                    cves.add(cve)
        return cls(
            id=int(data["id"]),
            name=str(data.get("display_name") or data.get("name") or ""),
            source=str(data.get("source") or ""),
            hosts_count=int(data.get("hosts_count") or 0),
            cve_count=len(cves),
        )


@dataclass(frozen=True, slots=True)
class FleetVulnerableSoftware:
    """The vulnerable-software summary the sensor is built from."""

    count: int
    counts_updated_at: datetime | None
    worst: list[FleetSoftwareTitle]


@dataclass(frozen=True, slots=True)
class FleetActivity:
    """One entry from Fleet's audit/activity feed."""

    id: int
    type: str
    created_at: datetime | None
    details: dict[str, Any]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FleetActivity:
        """Build an activity from a Fleet API payload."""
        return cls(
            id=int(data["id"]),
            type=str(data.get("type") or ""),
            created_at=parse_fleet_time(data.get("created_at")),
            details=data.get("details") or {},
        )


def _opt_float(value: Any) -> float | None:
    """Coerce to float, or None when Fleet has no value."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any) -> int | None:
    """Coerce to int, or None when Fleet has no value."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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
        # Resolved on the first policies fetch, then reused.
        self._policies_path: str | None = None

    @property
    def base_url(self) -> str:
        """The normalised Fleet base URL."""
        return self._base_url

    def host_page_url(self, host_id: int) -> str:
        """Deep link to a host's page in the Fleet UI."""
        return f"{self._base_url}/hosts/{host_id}"

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
                if response.status == 404:
                    raise FleetNotFoundError(
                        f"Fleet has no endpoint at {path} on this server version"
                    )
                response.raise_for_status()
                return await response.json()
        except (FleetAuthError, FleetForbiddenError, FleetNotFoundError):
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

        Tries each known spelling of the policies route until one answers, then
        remembers it, so a single Fleet version is only probed once.
        """
        candidates = (self._policies_path,) if self._policies_path else POLICY_PATHS
        last_error: FleetNotFoundError | None = None

        for path in candidates:
            try:
                policies = await self._async_read_policies(path)
            except FleetNotFoundError as err:
                last_error = err
                _LOGGER.debug("Fleet has no policies endpoint at %s", path)
                continue

            if self._policies_path != path:
                self._policies_path = path
                _LOGGER.debug("Using %s for Fleet global policies", path)
            return policies

        raise FleetError(
            "Could not find a policies endpoint on this Fleet server; tried "
            + ", ".join(POLICY_PATHS)
        ) from last_error

    async def _async_read_policies(self, path: str) -> list[FleetPolicy]:
        """Read every page of policies from a specific route.

        Paginated explicitly so a large policy library is never silently
        truncated by a server-side default page size.
        """
        policies: list[FleetPolicy] = []

        for page in range(MAX_PAGES):
            data = await self._get(
                path,
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

    async def async_get_hosts(self) -> list[FleetHost]:
        """Return every enrolled host.

        This is the expensive call in the integration, which is why it lives on
        the slower inventory coordinator rather than the summary one.
        """
        hosts: list[FleetHost] = []

        for page in range(MAX_PAGES):
            data = await self._get("/hosts", {"page": page, "per_page": HOSTS_PER_PAGE})
            batch = data.get("hosts") or []
            hosts.extend(FleetHost.from_json(host) for host in batch)
            if len(batch) < HOSTS_PER_PAGE:
                break
        else:
            _LOGGER.warning(
                "Stopped reading hosts at the %d page safety cap (%d hosts read). "
                "Some hosts will be missing from Home Assistant",
                MAX_PAGES,
                len(hosts),
            )

        return hosts

    async def async_get_vulnerable_software(self) -> FleetVulnerableSoftware:
        """Return the vulnerable software summary.

        A single request: Fleet reports the exact total in ``count`` and can
        order server-side, so the sample really is the worst titles by affected
        host count rather than whichever happened to come back first.
        """
        data = await self._get(
            "/software/titles",
            {
                "vulnerable": "true",
                "per_page": VULNERABLE_TITLES_SAMPLE,
                "order_key": "hosts_count",
                "order_direction": "desc",
            },
        )
        return FleetVulnerableSoftware(
            count=int(data.get("count") or 0),
            counts_updated_at=parse_fleet_time(data.get("counts_updated_at")),
            worst=[
                FleetSoftwareTitle.from_json(title)
                for title in (data.get("software_titles") or [])
            ],
        )

    async def async_get_activities(
        self, after_id: int | None = None
    ) -> list[FleetActivity]:
        """Return activities newer than ``after_id``, newest first.

        Fleet returns this feed newest-first, so the watermark lets us stop as
        soon as we reach something already seen instead of reading the whole
        audit history every poll. With no watermark yet, one page is enough to
        establish one.
        """
        collected: list[FleetActivity] = []

        for page in range(MAX_PAGES):
            data = await self._get(
                "/activities", {"page": page, "per_page": ACTIVITIES_PER_PAGE}
            )
            batch = data.get("activities") or []
            if not batch:
                break

            reached_watermark = False
            for item in batch:
                activity = FleetActivity.from_json(item)
                if after_id is not None and activity.id <= after_id:
                    reached_watermark = True
                    break
                collected.append(activity)

            # A short page means the feed is exhausted. No watermark means this
            # is the first read, and one page is all we need to set one.
            if (
                reached_watermark
                or after_id is None
                or len(batch) < ACTIVITIES_PER_PAGE
            ):
                break

        return collected
