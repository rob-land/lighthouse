# Snapshot release: CI passes -D "_snapver 0.<utc>git<sha>" so every build
# sorts above the last one and below a future tagged 1%%{?dist}.
%global snap %{?_snapver}%{!?_snapver:1}

Name:           lighthouse
Version:        0.1.0
Release:        %{snap}%{?dist}
Summary:        Find-my-device agent and viewer for Linux phones

License:        GPL-3.0-or-later
URL:            https://github.com/rob-land/lighthouse
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  meson >= 1.0.0
BuildRequires:  ninja-build
BuildRequires:  blueprint-compiler
BuildRequires:  gettext
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
BuildRequires:  glib2-devel
# blueprint-compiler resolves `using Gtk 4.0` / `using Adw 1` against the
# typelibs, which live in the -devel packages.
BuildRequires:  gtk4-devel
BuildRequires:  libadwaita-devel
BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
# %%check runs the loopback pair -> pin -> ring link test, which needs a
# real GIO TLS backend and the cert generator.
BuildRequires:  python3-gobject
BuildRequires:  python3-cryptography
BuildRequires:  glib-networking

Requires:       python3-gobject
Requires:       python3-cryptography
Requires:       gtk4
Requires:       libadwaita
Requires:       glib-networking
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
# wpctl is how the beam overrides silent mode; without it the ring still
# plays, just at whatever volume the phone was left on.
Recommends:     wireplumber
# mDNS discovery of KDE Connect peers.
Recommends:     avahi
Suggests:       kdeconnectd

%description
Lighthouse makes a Linux phone findable across several reach tiers and
renders the result natively, so Android and Linux phones appear in one
GTK viewer.

It is a node speaking existing protocols rather than a from-scratch
stack: it answers KDE Connect findmyphone pages on the LAN over a
mutually-authenticated, certificate-pinned TLS link, so a ring can be
triggered from stock KDE Connect, Valent or Plasma.

A page raises a full-screen takeover surface that rings past silent mode
— the tone is a raw PipeWire stream rather than a feedbackd event, and
the default sink is unmuted and raised for the duration, then restored.

This package installs the agent as a systemd user unit so the device
stays findable without the viewer running.

%prep
%autosetup -n %{name}-%{version}

%build
%meson
%meson_build

%install
%meson_install
# Enable the agent for new user sessions. Fedora presets default to
# disabled, so without this the unit ships inert and the GUI reports
# "not running" with no way to act.
install -Dpm0644 /dev/stdin \
  %{buildroot}%{_userpresetdir}/50-lighthouse.preset <<'EOF'
enable land.rob.lighthouse.service
EOF
%find_lang %{name} || :

%check
%meson_test

%post
%systemd_user_post land.rob.lighthouse.service

%preun
%systemd_user_preun land.rob.lighthouse.service

%files
%license COPYING
%doc README.md
%{_bindir}/lighthouse
%{python3_sitelib}/lighthouse/
%{_datadir}/applications/land.rob.lighthouse.desktop
%{_datadir}/metainfo/land.rob.lighthouse.metainfo.xml
%{_datadir}/glib-2.0/schemas/land.rob.lighthouse.gschema.xml
%{_datadir}/icons/hicolor/scalable/apps/land.rob.lighthouse.svg
%{_datadir}/icons/hicolor/symbolic/apps/land.rob.lighthouse-symbolic.svg
%{_datadir}/dbus-1/services/land.rob.lighthouse.Agent.service
%{_datadir}/lighthouse/
%{_userunitdir}/land.rob.lighthouse.service
%{_userpresetdir}/50-lighthouse.preset

%changelog
* Sun Aug 02 2026 Rob Land <thick.beach7895@fastmail.com> - 0.1.0-1
- Initial Fedora packaging.
