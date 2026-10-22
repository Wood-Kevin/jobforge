// No console window for the release build.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    jobforge_host::run()
}
