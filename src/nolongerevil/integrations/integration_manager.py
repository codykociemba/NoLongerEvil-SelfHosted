"""Integration manager for lifecycle management of integrations."""

import asyncio
import contextlib
from typing import TYPE_CHECKING

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceStateChange, IntegrationConfig

if TYPE_CHECKING:
    from nolongerevil.integrations.base_integration import BaseIntegration
    from nolongerevil.services.abstract_device_state_manager import AbstractDeviceStateManager
    from nolongerevil.services.device_state_service import DeviceStateService
    from nolongerevil.services.subscription_manager import SubscriptionManager

logger = get_logger(__name__)

# Polling interval for checking integration config changes
CONFIG_POLL_INTERVAL = 10  # seconds


class IntegrationManager:
    """Manages lifecycle of all integrations.

    - Loads enabled integrations from database
    - Initializes and shuts down integrations
    - Broadcasts state changes to active integrations
    - Polls for configuration changes
    """

    def __init__(
        self,
        storage: "AbstractDeviceStateManager",
        state_service: "DeviceStateService",
        subscription_manager: "SubscriptionManager | None" = None,
    ) -> None:
        """Initialize the integration manager.

        Args:
            storage: Storage backend for configuration
            state_service: Device state service for state access
            subscription_manager: Subscription manager for pushing updates to devices
        """
        self._storage = storage
        self._state_service = state_service
        self._subscription_manager = subscription_manager
        self._integrations: dict[str, BaseIntegration] = {}  # user_id:type -> integration
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the integration manager."""
        if self._running:
            return

        self._running = True
        await self._load_integrations()
        self._poll_task = asyncio.create_task(self._poll_config_loop())
        logger.info("Integration manager started")

    async def stop(self) -> None:
        """Stop the integration manager and all integrations."""
        self._running = False

        if self._poll_task:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None

        # Shutdown all integrations
        for key, integration in list(self._integrations.items()):
            try:
                await integration.shutdown()
                logger.info(f"Shut down integration: {key}")
            except Exception as e:
                logger.error(f"Error shutting down integration {key}: {e}")

        self._integrations.clear()
        logger.info("Integration manager stopped")

    async def _load_integrations(self) -> None:
        """Load and initialize enabled integrations from database."""
        configs = await self._storage.get_enabled_integrations()

        for config in configs:
            key = f"{config.user_id}:{config.type}"
            if key not in self._integrations:
                await self._create_integration(config)

    async def _create_integration(self, config: IntegrationConfig) -> None:
        """Create and initialize an integration.

        Args:
            config: Integration configuration
        """
        key = f"{config.user_id}:{config.type}"

        try:
            integration = self._instantiate_integration(config)
            if integration:
                await integration.initialize()
                self._integrations[key] = integration
                logger.info(f"Initialized integration: {key}")
        except Exception as e:
            logger.error(f"Failed to initialize integration {key}: {e}")

    def _instantiate_integration(self, config: IntegrationConfig) -> "BaseIntegration | None":
        """Instantiate an integration based on type.

        Args:
            config: Integration configuration

        Returns:
            Integration instance or None if unknown type
        """
        if config.type == "mqtt":
            from nolongerevil.integrations.mqtt import MqttIntegration

            return MqttIntegration(config, self._state_service, self._subscription_manager)

        logger.warning(f"Unknown integration type: {config.type}")
        return None

    async def _poll_config_loop(self) -> None:
        """Poll for configuration changes."""
        while self._running:
            try:
                await asyncio.sleep(CONFIG_POLL_INTERVAL)
                await self._check_config_changes()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in config poll loop: {e}")

    async def _check_config_changes(self) -> None:
        """Check for integration configuration changes."""
        configs = await self._storage.get_enabled_integrations()
        config_keys = {f"{c.user_id}:{c.type}" for c in configs}

        # Remove integrations that are no longer enabled
        for key in list(self._integrations.keys()):
            if key not in config_keys:
                integration = self._integrations.pop(key)
                try:
                    await integration.shutdown()
                    logger.info(f"Disabled integration: {key}")
                except Exception as e:
                    logger.error(f"Error disabling integration {key}: {e}")

        # Add new integrations
        for config in configs:
            key = f"{config.user_id}:{config.type}"
            if key not in self._integrations:
                await self._create_integration(config)

    async def on_device_state_change(self, change: DeviceStateChange) -> None:
        """Broadcast state change to all integrations.

        Args:
            change: State change event
        """
        for key, integration in self._integrations.items():
            try:
                await integration.on_device_state_change(change)
            except Exception as e:
                logger.error(f"Integration {key} failed on state change: {e}")

    async def on_device_connected(self, serial: str) -> None:
        """Broadcast device connected to all integrations.

        Args:
            serial: Device serial
        """
        for key, integration in self._integrations.items():
            try:
                await integration.on_device_connected(serial)
            except Exception as e:
                logger.error(f"Integration {key} failed on device connected: {e}")

    async def on_device_disconnected(self, serial: str) -> None:
        """Broadcast device disconnected to all integrations.

        Args:
            serial: Device serial
        """
        for key, integration in self._integrations.items():
            try:
                await integration.on_device_disconnected(serial)
            except Exception as e:
                logger.error(f"Integration {key} failed on device disconnected: {e}")

    def get_integration_count(self) -> int:
        """Get number of active integrations."""
        return len(self._integrations)

    def get_integration_keys(self) -> list[str]:
        """Get keys of active integrations."""
        return list(self._integrations.keys())
