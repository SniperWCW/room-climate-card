# Room Climate

Custom Home Assistant integration with bundled Lovelace card.

Features:

- room climate scoring per room
- separate advice for dehumidifying and cooling
- hourly ventilation window forecast from `weather.*`
- optional `sun.sun` and window orientation handling
- binary sensors for:
  - ventilate now
  - close window
  - close cover / roller shade
- optional push notifications via any `notify.*` service
- bundled `custom:room-climate-card` auto-loaded by the integration

## HACS

Add this repository as a custom integration repository in HACS and install it as an integration.

After restart:

1. Add the integration in Home Assistant.
2. Configure global entities and rooms via the integration options.
3. Use the Lovelace card type:

```yaml
type: custom:room-climate-card
```

You do not need to add a separate Lovelace resource manually. The integration registers the card automatically.

## Created Entities

For each configured room, the integration creates:

- `sensor.<room>_score`
- `sensor.<room>_recommendation`
- `binary_sensor.<room>_ventilate_now`
- `binary_sensor.<room>_close_window`
- `binary_sensor.<room>_close_cover`

## Notes

- Push notifications are sent only when the recommendation changes from inactive to active.
- A configurable cooldown prevents repeated notifications.
- The bundled Lovelace card is still useful for rich per-room display, while the integration handles backend logic and automation-friendly entities.
