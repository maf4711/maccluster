# Code- und Automatisierungsprüfung – 18. September 2026

Geprüft wurde der lokale Arbeitsbaum inklusive vorhandener, noch nicht eingecheckter Änderungen. Schwerpunkt: Wartungsautomatisierung, Updates, Healing, Prozesssteuerung und CI; kein vollständiger Sicherheits- oder Hardware-Audit.

## Verifikation

- `python3 -m pytest -q`: vollständig erfolgreich, Exit 0.
- `ruff check src tests`: erfolgreich.
- `ruff format --check src tests`: 307 Dateien bereits formatiert.
- CI-Matrix um Python 3.13 ergänzt, da diese Version in `pyproject.toml` ausgewiesen ist. Die zusätzliche GitHub-Matrix wurde lokal nicht ausgeführt.
- PID 96765 konnte wegen gesperrtem `ps` nicht geprüft werden. Kein Wartungslauf oder Deployment wurde gestartet.

## Bereits vorhanden

Unbeaufsichtigte Wartung mit gespeichertem Zeitplan, lokaler Sperre, einmaligem Wheel-Build pro Lauf, Hash- und Versionsprüfung, Installationsbelegen, Peer-Fehlerisolation, Heal-Loop und Watchdog. Optionaler Home-Sync hat SafetyNet und Verifikation. CI prüft Linux und macOS; Dependabot ist konfiguriert. Ein langlebiger Scandir-Hilfsprozess reduziert Prozessstarts und begrenzt blockierende Verzeichniszugriffe.

## Priorisierte Verbesserungen

Aufwand ist eine relative Einschätzung, keine Zeitzusage.

| Priorität | Befund und Codebeleg | Maßnahme und Nutzen | Aufwand |
|---|---|---|---|
| P1 | `services/package_update.py:22`: Standardquelle ist das veränderliche `main.zip`. Der selbst berechnete Hash identifiziert das gebaute Wheel, belegt aber keine Freigabe. | Geprüftes Release-Wheel mit festem Commit und erwartetem Digest veröffentlichen; Zeitplan an dieses Artefakt binden. Gleicher, freigegebener Stand bei jedem Rollout. | Mittel |
| P1 | `services/package_update.py:295`: aktive Installation wird unmittelbar ersetzt; danach wird nur die CLI-Version geprüft. `automation_service.py` führt den lokalen Doctor vor dem lokalen Update aus. | Neue Version in separater Umgebung vorbereiten, Smoke-Test und Gesundheitsprüfung ausführen, atomar umschalten und vorige Version für Rollback behalten. Ausfälle durch fehlgeschlagene Updates begrenzen. | Groß |
| P1 | `services/automation_service.py:134`: lokale Koordinatorsperre. `remote_install_script.py` hat keine entsprechende Installationssperre am Ziel. Die Dokumentation verlangt deshalb einen einzigen Koordinator. | Zielseitige Sperre, die Remote-Installation und lokale Updates gemeinsam verwenden; eindeutige Run-ID und begrenzte Wartezeit. Verhindert parallele Paketänderungen durch zwei Koordinatoren. | Mittel |
| P2 | `services/automation_service.py:199`: weitere Peers werden auch nach fehlgeschlagener Installation weiter aktualisiert. | Optional zuerst einen Peer aktualisieren und prüfen; bei Fehlschlag weitere Updates stoppen. Fehlerisolation für reine Diagnose weiterhin beibehalten. Begrenzt fehlerhafte Flottenrollouts. | Mittel |
| P2 | `services/heal_loop_service.py:44–65`: Fehlerdetails meist nur mit `verbose`, Heartbeat-Schreibfehler werden verschluckt. Der generierte Heal-Dienst aktiviert verbose nicht. | Zustandswechsel und Fehler strukturiert protokollieren; Wiederholungen zusammenfassen und bei dauerhaften Fehlern längere Wartezeiten vorsehen. Permanente Fehler werden nachvollziehbar, ohne Logflut. | Mittel |
| P2 | `services/automation_service.py:56`: nur letzter Laufbeleg. `adapters/plist_template.py` schreibt Dienst-Logs auf feste Pfade; die Rotation in `audit/log.py` gilt nur für das separate Audit-Log. | Begrenzte Laufhistorie mit Run-ID, Schrittdauer und Fehlerklasse; Rotation der Dienst-Logs und Aufbewahrungsgrenze. Unterstützt automatische Erkennung wiederkehrender Fehler und Laufzeitregressionen. | Mittel |
| P2 | `.github/workflows/ci.yml`: Lint und Tests, aber kein Wheel-/sdist-Installations-Smoke-Test und kein Aufruf des bereits als Dev-Abhängigkeit deklarierten `pip-audit`. | Build-Artefakte in frischer Umgebung installieren und CLI prüfen; separaten regelmäßigen Abhängigkeitsaudit ergänzen. Erkennt Packagingfehler und bekannte Abhängigkeitsprobleme automatisch. | Klein–mittel |
| P3 | `services/automation_service.py`: keine Nutzung des vorhandenen `busy_guard`; Peer-Wartung läuft seriell. | Wartungsfenster und Busy-Prüfung vor Update/Sync integrieren. Erst nach Messung unabhängige Diagnoseabfragen begrenzt parallelisieren; Updates kontrolliert staffeln. Vermeidet Störung aktiver Cluster-Arbeit. | Mittel |

## Sinnvolle Reihenfolge

Zuerst feste Release-Artefakte, zielseitige Sperren und abgesicherte Updates. Danach Fehlerhistorie, Dienst-Logrotation und Packaging-CI. Abschließend Wartungsfenster und anhand gemessener Schrittdauern gezielte Parallelisierung. Für Performance wurden keine Hardwaremessungen durchgeführt; eine konkrete Beschleunigung ist daher nicht belegt.

## Nachfolgend lokal umgesetzt

- Zielseitige Kernel-Sperre im Remote-Bootstrap: gleicher Pfad und gleiches Sperrverfahren wie lokale Wartung, begrenzte Wartezeit, keine Abhängigkeit von bereits installiertem MacCluster. Gegenseitiger Ausschluss, Freigabe nach Prozessabbruch und Symlink-Abwehr mit echten Prozessen getestet. Bestehende Installationen müssen die neue Version erst erhalten; ältere Koordinatoren senden noch das ungeschützte Skript.
- CI baut Wheel und sdist und installiert beide separat in frischen virtuellen Umgebungen. CLI-Version und Hilfe werden außerhalb des Checkouts geprüft.
- Separater Abhängigkeitsaudit für Dev- und Monitor-Abhängigkeiten; wöchentlicher und manueller Workflow-Trigger ergänzt. Dieser Audit benötigt Netzwerk und wurde hier nicht ausgeführt.
- Gesamte lokale Testsuite nach Codeänderung erfolgreich: 766 Tests. Ruff und Formatprüfung erfolgreich. GitHub-Actions-Ausführung steht aus.

## Zweiter Umsetzungsschritt

`update`, `automation run` und `automation install` unterstützen jetzt `--sha256`
für lokale Release-Wheels. Ein unverändert zwischengespeichertes Wheel wird gegen
den vorgegebenen Digest geprüft, bevor es zur Paketinstallation gelangt. Zeitpläne
und Laufberichte speichern den erwarteten Digest; alte Zeitpläne bleiben lesbar.
Tests prüfen Manipulation, ungültige Hashes/Quellen, Weitergabe vom CLI und
Kompatibilität gespeicherter Einstellungen.

Dies ist eine optionale Festlegung auf ein Artefakt. Der bisherige Standard
`main.zip` bleibt erhalten; eine veröffentlichte Release-Pipeline und automatische
Rollback-Funktion sind weiterhin offen. Für Rollback fehlen bisher dauerhaft
aufbewahrte Vorgänger-Artefakte und ein gemeinsamer Wiederherstellungspfad für
pipx sowie gewöhnliche virtuelle Umgebungen.

## Dritter Umsetzungsschritt – 19. September 2026

Lokale Updates bewahren Wheels dauerhaft nach Digest auf. Bei einem späteren
Installationsfehler oder einer falschen CLI-Version kann die verifizierte
Vorgängerversion automatisch wieder installiert werden. Voraussetzung: Beleg,
aufbewahrtes Wheel und aktive Installation stimmen vor dem Update überein.
Wiederherstellung prüft Version und Digest; der Update-Lauf bleibt als fehlgeschlagen
erkennbar. Korruption des Vorgänger-Wheels blockiert das Update.

Fehlerfälle beider Installationswege werden mit simulierten Paketmanagern getestet;
ein zusätzlicher Integrationstest installiert und ersetzt Wheels mit echtem pip in
einer temporären venv und prüft die tatsächliche Wiederherstellung. Ein echter
pipx-Rollout wurde nicht durchgeführt.

Grenzen: keine garantierte Wiederherstellung beim ersten Update, ohne passendes
Vorgänger-Artefakt oder nach Installer-Timeout. Kein Remote-Bootstrap-Rollback,
kein atomarer Umgebungswechsel, kein Konfigurations-/Daten-Rollback und keine
automatische Bereinigung der aufbewahrten Wheels. Die Release-Veröffentlichung
bleibt offen. Vorherige Abschnitte dokumentieren den jeweiligen damaligen Stand.

## Vierter Umsetzungsschritt – 21. September 2026

Flottenupdates stoppen nach dem ersten erfolglosen Peer. Nachfolgende Peers,
optionaler Sync und das lokale Paketupdate werden mit einer Begründung im Bericht
übersprungen; die lokale Diagnose läuft weiter. Dies gilt für negative
Installationsresultate, Ausnahmen und verschlechterten Peer-Zustand. Erfolgreiche
Läufe verwenden weiterhin ein gemeinsames Wheel und aktualisieren den lokalen
Koordinator zuletzt. Reine Heilung mit `--no-update` versucht alle Peers.

Gezielte Tests decken Fehler beim ersten und letzten Peer, mehrere Fehlerarten,
erfolgreiche Rollouts sowie Fortsetzung reiner Heilung ab. Bereits aktualisierte
Peers werden bei einem späteren Peer-Fehler nicht gemeinsam zurückgerollt. Die
Reihenfolge kommt aus der Cluster-Konfiguration; es gibt noch keine gesonderte
Auswahl eines Test-Peers oder eine Beobachtungsphase nach dem Update.
