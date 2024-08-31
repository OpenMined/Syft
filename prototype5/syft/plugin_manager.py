"""
Welcome to the Magical Plugin Playground! 🎪✨

Toy Box Story:
Imagine you have a magical toy box filled with all sorts of wonderful toys (we call them plugins).
Each toy has its own special abilities and can play on its own little track. You have a friendly
robot helper (the PluginManager) who keeps everything organized. This robot can:
- Unpack new toys and figure out how they work
- Set up tracks for the toys to play on
- Watch the toy box for any changes or upgrades to the toys
- Help clean up when playtime is over

Reality:
In the world of software, this magical toy box is actually a powerful plugin system. The PluginManager
is a sophisticated piece of software that manages the lifecycle of plugins in an application. It provides:
- Dynamic loading and unloading of plugin modules
- Concurrent execution of plugins in separate threads
- Hot-reloading of plugins when their source code changes
- Resource management and cleanup

This plugin system allows for a flexible and extensible architecture, enabling developers to easily
add new functionality to the application without modifying the core codebase. The PluginManager
handles all the complexities of plugin discovery, initialization, execution, and termination,
providing a seamless integration of plugins into the main application.

Let's dive into the magical world of plugins and see how this playground works! 🚀
"""

import importlib
import logging
import os
import sys
import threading
from typing import Any, Dict, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .shared_state import SharedState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

"""
Step 1: The Toy Runner 🏃‍♂️ (Thread Creation)

Toy analogy: This is like a special track where each toy can run and play.

Reality: We create a separate thread for each plugin to run independently.
This allows multiple plugins to operate concurrently without interfering with each other.
"""


class PluginThread(threading.Thread):
    def __init__(self, plugin: Any, data: Dict[str, Any], shared_state: SharedState):
        """
        Step 1a: Setting Up the Track

        Toy analogy: We're setting up the track for our toy to run on.

        Reality: We're initializing the thread with the plugin, its data, and shared state.
        """
        super().__init__()
        self.plugin: Any = plugin
        self.data: Dict[str, Any] = data
        self.shared_state: SharedState = shared_state
        self.stop_event: threading.Event = threading.Event()
        self.daemon: bool = True

    def run(self) -> None:
        """
        Step 1b: Let the Toy Play! 🎉 (Plugin Execution)

        Toy analogy: This is where we let our toy run and have fun on its track.

        Reality: This method executes the plugin's main functionality.
        We use a try-except block to catch and log any errors that occur during execution.
        """
        try:
            # Let the toy (plugin) play with its accessories (data) following the playground rules (shared state)
            self.plugin.execute(self.data, self.shared_state)

        except Exception as e:  # pragma: no cover
            # If the toy breaks (an error occurs), we log what happened
            logger.error(
                f"Uh oh! Plugin {self.plugin.__name__} had a problem: {e}"
            )  # pragma: no cover

    def stop(self) -> None:
        """
        Step 1c: Nap Time for Toys 😴 (Thread Termination)

        Toy analogy: When it's time to rest, we gently tell our toy to stop playing.

        Reality: This method sets a flag to signal the thread to terminate its execution.
        """
        # Ring the bell to signal that playtime is over
        self.stop_event.set()


"""
Step 2: The Toy Box Manager 📦 (Plugin Management)

Toy analogy: This is like a friendly robot that helps you manage all your toys.

Reality: The PluginManager class is responsible for loading, starting, stopping,
and reloading plugins. It keeps track of all active plugins and their threads.
"""


class PluginManager:
    def __init__(self, plugin_dir: str):
        """
        Step 2a: Setting Up the Toy Box

        Toy analogy: Setting up our toy box.

        Reality: We're initializing the PluginManager with the directory where plugins are stored.
        """
        self.plugin_dir: str = plugin_dir
        self.plugins: Dict[str, Any] = {}
        self.plugin_threads: Dict[str, PluginThread] = {}
        self.lock: threading.Lock = threading.Lock()
        self.observer: Optional[Observer] = None
        self.shared_state: SharedState = SharedState()

    def load_plugins(self) -> None:
        """
        Step 2b: Unpacking the Toy Box 🧸 (Plugin Discovery)

        Toy analogy: We open the toy box and look at all the toys we have.

        Reality: This method scans the plugin directory and attempts to load each .py file as a plugin.
        """
        with self.lock:  # Make sure no one messes with our toy box while we're looking inside
            for root, dirs, files in os.walk(self.plugin_dir):
                for filename in files:
                    if filename.endswith(".py") and filename != "__init__.py":
                        plugin_path = os.path.join(root, filename)
                        self.load_plugin(plugin_path)

    def load_plugin(self, plugin_path: str) -> None:
        """
        Step 2c: Inspecting a New Toy 🔍 (Plugin Loading)

        Toy analogy: We carefully look at a new toy to see how it works.

        Reality: This method loads a single plugin from the specified path.
        It imports the plugin module and wraps its execute function in a PluginWrapper.
        """
        try:
            plugin_name = os.path.basename(plugin_path).replace(".py", "")
            logger.debug(f"Attempting to load plugin: {plugin_name}")
            logger.debug(f"Plugin path: {plugin_path}")

            if not os.path.exists(plugin_path):
                logger.warning(f"Plugin file not found: {plugin_path}")
                return

            spec = importlib.util.spec_from_file_location(plugin_name, plugin_path)  # type: ignore
            if spec is None:
                logger.error(
                    f"Failed to create spec for plugin: {plugin_name}"
                )  # pragma: no cover
                return  # pragma: no cover

            module = importlib.util.module_from_spec(spec)  # type: ignore
            if module is None:
                logger.error(
                    f"Failed to create module for plugin: {plugin_name}"
                )  # pragma: no cover
                return  # pragma: no cover

            spec.loader.exec_module(module)

            # Wrap the execute function in a simple object
            class PluginWrapper:
                def __init__(self, execute_func):
                    self.execute = execute_func

            # Check if the module has an execute function
            if hasattr(module, "execute") and callable(module.execute):
                self.plugins[plugin_name] = PluginWrapper(module.execute)
            else:
                raise AttributeError(
                    f"Plugin {plugin_name} does not have an 'execute' function"
                )  # pragma: no cover

            logger.info(f"Successfully loaded plugin: {plugin_name}")
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to load plugin {plugin_name}: {str(e)}")

    def start_plugin_thread(self, plugin_name: str) -> None:
        """
        Step 2d: Playtime Begins! 🎮 (Thread Initialization)

        Toy analogy: We put a toy on its special track and say "Go! Have fun!"

        Reality: This method creates and starts a new thread for a specific plugin.
        """
        if plugin_name not in self.plugins:
            logger.warning(f"{plugin_name} is not in our toy box. We can't start it.")
            return

        plugin = self.plugins[plugin_name]
        thread = PluginThread(plugin, {"name": "World"}, self.shared_state)
        thread.start()
        self.plugin_threads[plugin_name] = thread
        logger.info(f"{plugin_name} is now playing on its track!")

    def stop_plugin_thread(self, plugin_name: str) -> None:
        """
        Step 2e: Cleanup Time 🧹 (Thread Termination)

        Toy analogy: When you're done playing with a toy, we help put it away nicely.

        Reality: This method stops the execution of a plugin's thread and removes it from the active threads.
        """
        if plugin_name in self.plugin_threads:
            thread = self.plugin_threads[plugin_name]
            thread.stop()
            thread.join(timeout=1)
            del self.plugin_threads[plugin_name]
            logger.info(f"{plugin_name} is now resting in the toy box.")

    def reload_plugin(self, plugin_name: str) -> None:
        """
        Step 2f: Toy Makeover ✨ (Plugin Reloading)

        Toy analogy: Sometimes we can make old toys feel like new!

        Reality: This method reloads a plugin by stopping its current thread, reimporting the module,
        and starting a new thread with the updated code.
        """
        with self.lock:
            try:
                self.stop_plugin_thread(plugin_name)

                module_name = f"plugins.{plugin_name}"
                if module_name in sys.modules:
                    module = sys.modules[module_name]
                    importlib.reload(module)
                    self.plugins[plugin_name] = module
                else:
                    self.load_plugin(plugin_name)

                logger.info(f"{plugin_name} got a makeover and is ready to play again!")
            except Exception as e:  # pragma: no cover
                error_message = f"Oops! We couldn't give {plugin_name} a makeover. Here's why: {str(e)}"  # pragma: no cover
                logging.error(
                    error_message
                )  # Make sure this line exists # pragma: no cover
                # You might want to re-raise the exception or handle it differently depending on your needs

    def start_watchdog(self) -> None:
        """
        Step 2g: The Toy Guardian 🐶 (File System Watcher)

        Toy analogy: We have a special puppy that watches the toy box.

        Reality: This method starts a file system observer that monitors the plugin directory for changes.
        """
        event_handler = PluginReloader(self)
        self.observer = Observer()
        self.observer.schedule(event_handler, self.plugin_dir, recursive=False)
        self.observer.start()
        logger.info("Our toy box watcher is now on duty!")

    def stop_watchdog(self) -> None:
        """
        Step 2h: Puppy Nap Time 🐾 (Stopping File System Watcher)

        Toy analogy: When we're done playing, we let our watchdog puppy take a nap.

        Reality: This method stops the file system observer when it's no longer needed.
        """
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=1)
            logger.info("Our toy box watcher is taking a nap.")

    def cleanup(self) -> None:
        """
        Step 2i: The Big Cleanup 🧼 (Resource Management)

        Toy analogy: Before we go to bed, we make sure all toys are put away nicely.

        Reality: This method ensures all plugin threads are stopped and resources are properly released.
        """
        self.stop_watchdog()
        for plugin_name in list(self.plugin_threads.keys()):
            self.stop_plugin_thread(plugin_name)
        logger.info("All toys are now resting in the toy box.")

    def handle_plugin_change(self, filename: str) -> None:
        """
        Step 2j: Toy Upgrade Detector 🕵️‍♂️ (File Change Handler)

        Toy analogy: When we notice a toy has changed, we give it special attention.

        Reality: This method is called when a file in the plugin directory is modified.
        It reloads the affected plugin if it's a Python file.
        """
        if filename.endswith(".py"):
            plugin_name = os.path.splitext(os.path.basename(filename))[0]
            self.reload_plugin(plugin_name)
        else:
            logger.info(f"Ignoring non-Python file: {filename}")

    def execute_plugins(self) -> None:
        """
        Step 2k: Playtime for All! 🎭 (Plugin Execution)

        Toy analogy: We let all the toys in our toy box play together.

        Reality: This method executes all loaded plugins sequentially.
        It catches and logs any exceptions that occur during plugin execution.
        """
        for plugin_name, plugin in self.plugins.items():
            try:
                logger.info(f"Executing plugin: {plugin_name}")
                plugin.execute({}, self.shared_state)
            except Exception as e:
                logger.error(f"Error executing plugin {plugin_name}: {e}")


"""
Step 3: The Toy Upgrade Detector 🕵️‍♂️ (File Change Handler)

Toy analogy: This is like a detective that notices when toys get upgrades.

Reality: This class extends FileSystemEventHandler to detect changes in plugin files.
"""


class PluginReloader(FileSystemEventHandler):
    def __init__(self, plugin_manager: PluginManager):
        """
        Step 3a: Giving the Detective a Walkie-Talkie

        Toy analogy: We give our detective a walkie-talkie to talk to the Toy Box Manager.

        Reality: We're passing a reference to the PluginManager for communication.
        """
        self.plugin_manager = plugin_manager  # Our walkie-talkie to the Toy Box Manager

    def on_modified(self, event):
        """
        Step 3b: Upgrade Spotted! 📢 (File Modification Event)

        Toy analogy: When the detective sees a toy change, it shouts "Upgrade!" into the walkie-talkie.

        Reality: This method is called when a file in the plugin directory is modified,
        triggering the plugin manager to reload the affected plugin.
        """
        logger.debug(f"File modified: {event.src_path}")
        filename = os.path.basename(event.src_path)
        logger.debug(f"Extracted filename: {filename}")
        self.plugin_manager.handle_plugin_change(filename)


# This PluginManager system allows for dynamic loading, unloading, and reloading of plugins,
# enabling a flexible and extensible application architecture.

"""
Congratulations, intrepid explorer! 🎉🚀

You've successfully navigated the magical world of the PluginManager. You've seen how we load,
manage, and execute plugins, and even how we keep an eye on them for changes.

What's next on your adventure, you ask?

Head over to syft/shared_state.py to discover how we manage shared state across our plugins.
There, you'll learn about the mystical SharedState class that allows our plugins to communicate
and share information.

Remember, in the world of coding, every line is a new adventure. Happy exploring!
"""
