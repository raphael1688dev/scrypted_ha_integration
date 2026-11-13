# Scrypted HA(Mod)

A modified fork of [koush/ha_scrypted](https://github.com/koush/ha_scrypted),
the Scrypted custom component for Home Assistant. It adds support for managing
Scrypted from your Home Assistant Dashboard, and creation of Scrypted NVR
cards.

![Scrypted Dashboard](https://github.com/koush/ha_scrypted/assets/73924/b76b239a-a61a-451a-84aa-1d3621594a68)

Visit the [Scrypted Documentation](https://docs.scrypted.app/home-assistant.html)
for setup instructions. Requires Home Assistant 2026.11 or newer.

## Device entities

When *Create entities for Scrypted devices* is enabled (default), the
integration connects to the Scrypted server over its engine.io RPC API and
creates entities for discovered devices. By default only **Camera** and
**Doorbell** device types are mirrored; pick additional Scrypted device types
in the integration options. Smart Motion and Smart Occupancy Sensors created
by Scrypted's object detection plugin are always mirrored. They trigger only
on the object classes you choose (e.g. people, not swaying trees), which makes
them a good fit for automations. Other `Sensor` devices are left out by default
because they are often bridged from integrations Home Assistant already has
(Ring, UniFi Protect, MQTT).

To combine broad device types with specific sources, exclude plugins in the
options: devices from an excluded plugin never get entities, whatever their
type. For example, select **Sensor** but exclude the Ring plugin if Home
Assistant's Ring integration already covers those sensors. Excluding the
object detection plugin also hides its smart sensors.

Narrowing the options (unselecting a device type or excluding a plugin)
removes the devices they now hide, along with their entities, when the
integration reloads. Devices that Scrypted itself no longer reports are kept,
since they may only be missing briefly; delete those from the device page once
you're sure they're gone.

- **Camera** — snapshots for `VideoCamera` devices. For live video use the
  bundled camera card (below) or the Scrypted NVR cards.
- **Binary sensor** — motion, doorbell button, sound, occupancy, flood, entry,
  power, connectivity, tamper, charging, sleeping.
- **Detected binary sensors** — one per object class a camera's detector
  reports (*Person detected*, *Car detected*, …). Each turns on when the class
  is detected and off after 60 seconds without another detection, like
  Scrypted's Smart Motion Sensor but with no setup. They are created disabled,
  since a detection model can report dozens of classes: enable the ones you
  want from the camera's device page.
- **Event** — object detection (`person`, `car`, … from the detector's
  reported classes) and doorbell presses, usable as automation triggers.
- **Sensor** — temperature, humidity, battery, illuminance, UV, CO2,
  PM2.5/PM10, VOC, NOx, air quality.
- **Button** — *Restart* for devices that support rebooting, such as most
  cameras. Created disabled; enable it from the device page.
- **Media player** — intercom-capable devices (doorbells, two-way cameras)
  become speaker entities: point `media_player.play_media`, TTS, or announce
  at them to talk through the camera speaker.
- **Switch, lock, cover, light, fan, vacuum, climate** — controllable
  Scrypted devices of those types, once their device type is enabled in the
  options.
- **Notification switches** — enable the **Notifier** device type to get a
  switch per Scrypted NVR app (one per user per phone or Mac) that mutes its
  push notifications. Off by default: anyone with Home Assistant access could
  then mute any household member's notifications.

Scrypted NVR detection clips are also available in the Home Assistant media
browser (camera → day → clip) and play through HA's stream component.

State updates are pushed; no polling. Entities reconnect automatically if the
server restarts.

## Live video: the bundled camera card

The integration includes
[scrypted-camera-card](https://github.com/raphael1688dev/scrypted-camera-card),
a dashboard card that plays a Scrypted camera over WebRTC, with talkback and
fullscreen. It needs no Scrypted NVR subscription and no separate install: the
card is loaded on every dashboard as soon as the integration is set up.

```yaml
type: custom:scrypted-camera-card
device: Front Door  # Scrypted device name or id
```

The Scrypted device id is also shown as the `device_id` attribute of each
camera entity. See the card's README for every option.

The card remains a separate project and can still be installed on its own.
If it is registered as a dashboard resource (a Marketplace install, or a build
you are developing under `/local/`), that copy is used and the bundled one is
not loaded, so the two never run together. To switch to the bundled card,
delete the resource under **Settings > Dashboards > Resources**, uninstall the
card from the Marketplace, then reload the integration and the browser page.

The bundled file is the unmodified release build of the card, which is MIT
licensed (`custom_components/scrypted/frontend/LICENSE.scrypted-camera-card`).
A scheduled workflow (`.github/workflows/update-card.yaml`) opens a pull
request here whenever the card repository publishes a new full release; it
runs `python scripts/update_card.py vX.Y.Z`, which can also be run by hand.
A new card reaches users with the next integration release, after a Home
Assistant restart.

## Setup and settings

Setup asks for the Scrypted host (`192.168.1.124`, or `192.168.1.124:10443`
for another port), username and password. Everything else is under
**Configure**: the sidebar panel's title and icon, the Scrypted NVR options
and which devices get entities. Entries created before 1.0.2 move their panel
title and icon there automatically.

- Each Scrypted server can be added once. Servers are recognised by host, so
  `192.168.1.124` and `192.168.1.124:10443` count as the same one.
- When the stored credentials stop working, Home Assistant asks for a new
  username and password. That form keeps the host.
- **Reconfigure** changes the host or the account. Leave the password empty to
  keep the current one. Scrypted has no server ID the integration could check,
  so it cannot tell whether a new address is the same server: only change it
  when the server moved.

## Security

The integration logs in to Scrypted as the account you configure, and every
part of it acts as that account. The sidebar panel, the camera card and the
media browser reach Scrypted through Home Assistant at
`/api/scrypted/<token>/…`. That path needs no Home Assistant login: the token
in it is the credential.

- Every Home Assistant user, administrator or not, can open the sidebar panel
  and so obtain the token. The panel stays open to all users because the
  camera card finds Scrypted through it, including on non-administrator
  accounts such as wall tablets.
- The diagnostic **Token** sensor shows the same token, so it can be copied
  into Scrypted's notifier settings. Home Assistant's recorder keeps its
  history; to stop that, exclude it in `configuration.yaml`:

  ```yaml
  recorder:
    exclude:
      entity_globs:
        - sensor.scrypted*token*
  ```

Treat every Home Assistant account as having the configured Scrypted
account's access. If that is too much, set the integration up with a Scrypted
user that only has the permissions you want Home Assistant to have.

## Development

### Using Dev Container (Recommended)

The easiest way to develop is using the included dev container:

1. Install [VS Code](https://code.visualstudio.com/) and the
   [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
2. Open this repository in VS Code
3. Click "Reopen in Container" when prompted (or run the command manually)
4. Wait for the container to build and dependencies to install
5. Home Assistant will start automatically at <http://localhost:8123>

The dev container automatically:

- Installs all dependencies
- Sets up pre-commit hooks
- Creates a `config/` directory (git-ignored) for Home Assistant and symlinks
  `custom_components` into it
- Configures debug logging for the integration
- Starts debugpy on port 5678 (attach with the provided VS Code launch config).
  To make Home Assistant wait for the debugger on startup, copy
  `.devcontainer/.env.example` to `.devcontainer/.env` and set
  `ENV_DEBUGPY_WAIT=true`.

### Local Development

To set up a local development environment:

```bash
# Create and activate virtual environment
python3.14 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements_dev.txt

# Install pre-commit hooks
pre-commit install

# Run tests
pytest

# Run linting and formatting checks
ruff check .
ruff format --check .
```
