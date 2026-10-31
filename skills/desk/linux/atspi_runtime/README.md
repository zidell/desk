# App-private AT-SPI runtime

Pinned upstream source: AT-SPI 2.60.4, LGPL-2.1-or-later, from
https://download.gnome.org/sources/at-spi2-core/2.60/at-spi2-core-2.60.4.tar.xz
The archive includes the upstream license and complete source.
Meson 1.9.1 is bundled under its Apache-2.0 license (included in the wheel).
Input SHA-256 hashes are in checksums.json. Builds need no network access.

transport.patch makes clients use the existing authenticated accessibility bus
instead of peer connections, which leak credentials/pidfds in older toolkit
bridges. It also backports upstream direct-connection Disconnected cleanup to
the GTK bridge. Upstream implementation/reference:
https://github.com/GNOME/at-spi2-core/blob/main/atspi/atspi-misc.c
https://github.com/GNOME/at-spi2-core/blob/main/atk-adaptor/bridge.c
https://gitlab.gnome.org/GNOME/at-spi2-core/-/merge_requests/235

This preserves accessibility queries and the normal per-user D-Bus permission
boundary. It neither disables accessibility nor changes system libraries,
system services, or login startup. Only the app's private runtime is loaded.

Build with `python3 build.py OUTPUT [--bridge]`. Dependencies: C compiler,
pkg-config, ninja, patch, patchelf, GLib/GIO and D-Bus development headers;
bridge builds also require ATK, GModule and libxml2 development headers.
The private libraries are dynamically linked and may be rebuilt/replaced from
the supplied sources. No GTK service/daemon or desktop files are installed.
