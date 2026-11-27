# Installing Pennant

Pennant Backup copies chosen folders to a second disk or to a server on your network, and keeps older versions of every file so that a mistake can be undone.

## System requirements

Pennant runs on any computer with at least four gigabytes of memory and two gigabytes of free disk space for the program and its settings.

The backup disk should be at least as large as the folders you want to protect, and twice as large if you want to keep a year of older versions.

### Download and verify

Download the installer from the product page and compare the checksum shown there with the one your computer reports before you open the file.

### Run the installer

Double-click the installer, accept the licence and choose the folder for the program. The installer takes about a minute and does not need a restart.

## First start

# Making backups

A backup is a copy of your chosen folders made at a time you choose. The first backup copies everything, and later ones copy only what has changed.

## Choosing folders

Press Add folder and pick the folders to protect. Pennant skips temporary files and the recycle bin unless you tell it to include them.

Large video files are copied in pieces so that a failed copy can carry on from the point where it stopped instead of starting again.

## Setting a schedule

Open the Schedule tab and choose how often to run: every hour, every night or every week. A backup that is missed because the computer was off runs as soon as it is switched on again.

Nightly backups start at one in the morning by default, and you can move the start time to any hour when you are unlikely to be working.

### Pausing a backup

Press Pause on the tray icon to stop a running backup. It will carry on from the same place when you press Resume.

# Getting files back

To get a file back, open the Restore tab, find the file by name or by the date it was last saved, and press Restore.

## Restoring older versions

Pennant keeps thirty daily versions, twelve monthly versions and every yearly version of each file. Choose the version by its date and restore it beside the current file so that nothing is overwritten.

## Troubleshooting

If a backup fails, open the Log tab. Each failed file is listed with the reason, and most failures are caused by a file that was open in another program or by a disk that is full.

If the backup disk is not recognised, unplug it and plug it in again, and then press Rescan in the Settings tab.
