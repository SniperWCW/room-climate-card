# Room Climate Card

Custom Lovelace card for Home Assistant with:

- room climate scoring
- separate advice for dehumidifying and cooling
- optional ventilation time forecast based on weather data
- editor support for room sensors and `thermal_comfort` entities

## HACS

Add this repository as a custom dashboard repository in HACS and install it as a frontend card.

After installation, use the card type:

```yaml
type: custom:room-climate-card
```

## Expected Entities

Per room, the card can use:

- temperature
- relative humidity
- inside absolute humidity
- humidex value
- humidex felt
- summer scharlau felt
- summer simmer felt
- dewpoint felt
- optional window sensor

Global entities:

- outside absolute humidity
- optional `weather.*` entity for wind and cooling forecast

## Notes

- Dehumidifying and cooling advice are shown separately.
- If forecast data is available on the selected weather entity, the card shows the next likely ventilation window.
