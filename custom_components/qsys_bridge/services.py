"""Services for the Q-SYS Core bridge integration."""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol
from homeassistant.core import (
    HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import yaml

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_RESCAN = "rescan"
SERVICE_GENERATE_CARD = "generate_zone_card"
ATTR_TARGET = "core"
ATTR_GROUPS = "groups"

# The platforms a Q-SYS control can become, in the order a zone role wants
# them: a level is a number, a mute a switch.
_PLATFORMS = ("number", "switch", "sensor", "binary_sensor", "text")


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_RESCAN):
        return

    async def handle_rescan(call: ServiceCall) -> ServiceResponse:
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("The bridge is not set up")
        coordinator = next(iter(entries.values()))

        body = {}
        target = (call.data.get(ATTR_TARGET) or "").strip()
        if target:
            body[ATTR_TARGET] = target

        session = async_get_clientsession(hass)
        try:
            async with session.post(
                f"{coordinator.url}/api/rediscover", json=body,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                response.raise_for_status()
                result = await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the add-on: {err}") from err

        # The add-on holds the connection, so it does the work; refreshing
        # here is what turns anything new into entities without a wait.
        await coordinator.async_request_refresh()
        return result

    hass.services.async_register(
        DOMAIN, SERVICE_RESCAN, handle_rescan,
        schema=vol.Schema({vol.Optional(ATTR_TARGET): str}),
        supports_response=SupportsResponse.ONLY,
    )

    async def handle_generate_zone_card(call: ServiceCall) -> ServiceResponse:
        """Build an audio-zone-card for the zones the Core exposes.

        The add-on knows which control is a zone's level and which is its
        mute, but not what those controls became here. Only the registry
        knows that, so the substitution happens on this side.
        """
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("The bridge is not set up")
        coordinator = next(iter(entries.values()))

        params = {}
        groups = call.data.get(ATTR_GROUPS)
        if groups:
            params[ATTR_GROUPS] = ",".join(groups)

        session = async_get_clientsession(hass)
        try:
            async with session.get(
                f"{coordinator.url}/api/cards/zones", params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                payload = await response.json()
                if response.status != 200:
                    raise HomeAssistantError(
                        payload.get("error", f"Add-on returned {response.status}")
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the add-on: {err}") from err

        card = payload.get("card")
        if not card:
            raise HomeAssistantError("The add-on produced no card")

        registry = er.async_get(hass)
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for role, key in card.get("entities", {}).items():
            entity_id = _entity_id_for_key(registry, coordinator, key)
            if entity_id:
                resolved[role] = entity_id
            else:
                missing.append(role)

        if not resolved:
            raise HomeAssistantError(
                "None of the exposed zone controls have entities here — "
                "expose them in the add-on first"
            )

        card["entities"] = resolved
        # A strip whose level did not resolve would draw an empty row, so
        # renumber around the zones that did and shrink the rack to suit.
        card = _compact_zones(card)

        return {
            "card": card,
            "yaml": yaml.safe_dump(
                card, default_flow_style=False, sort_keys=False,
                allow_unicode=True, width=10000,
            ),
            "unresolved_roles": sorted(missing),
            "cores": sorted(getattr(coordinator, "cores", {}) or {}),
        }

    hass.services.async_register(
        DOMAIN, SERVICE_GENERATE_CARD, handle_generate_zone_card,
        schema=vol.Schema({vol.Optional(ATTR_GROUPS): [str]}),
        supports_response=SupportsResponse.ONLY,
    )


def _entity_id_for_key(registry, coordinator, key: str) -> str | None:
    """Find what a control key became, by the unique id the entity minted."""
    # The bridge can hold more than one Core and a key is unique only
    # within one, so every Core is tried.
    for core in getattr(coordinator, "cores", None) or {}:
        unique_id = f"{DOMAIN}_{core}_{key}"
        for platform in _PLATFORMS:
            entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
            if entity_id:
                return entity_id
    return None


def _compact_zones(card: dict) -> dict:
    """Renumber the strips so there are no gaps, and resize the rack.

    A zone whose level control has no entity cannot be drawn. Leaving the
    numbering alone would put a blank strip in the middle of the rack,
    which reads as a zone that has gone silent.
    """
    entities = card.get("entities", {})
    labels = card.get("labels", {})
    keep = [
        index for index in range(1, 17)
        if f"zone{index}_volume" in entities
    ]
    new_entities: dict[str, str] = {}
    new_labels: dict[str, str] = {}
    for position, index in enumerate(keep, start=1):
        for suffix in ("volume", "mute"):
            value = entities.get(f"zone{index}_{suffix}")
            if value:
                new_entities[f"zone{position}_{suffix}"] = value
        if labels.get(f"zone{index}"):
            new_labels[f"zone{position}"] = labels[f"zone{index}"]
    card["entities"] = new_entities
    if new_labels:
        card["labels"] = new_labels
    card["options"] = {"zones": max(1, len(keep))}
    return card


def async_unload_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_RESCAN)
