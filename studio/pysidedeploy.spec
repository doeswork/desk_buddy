[app]
title = DeskBuddyStudio
project_dir = ..
input_file = __main__.py
project_file =
exec_directory = .
icon =

[python]
python_path =
packages = Nuitka

[qt]
# Only the Qt modules we actually use. Keeps the bundle small.
modules = Core,Gui,Widgets,SerialPort
plugins = platforms,platforminputcontexts,styles
qml_files =

[nuitka]
mode = onefile
# --windows-console-mode=disable stops a terminal opening behind the GUI on Windows.
extra_args = --quiet --noinclude-qt-translations --windows-console-mode=disable --include-data-dir=firmware=firmware --include-data-dir=studio/.build/vision-bootstrap=studio/services/vision/assets

[buildozer]
