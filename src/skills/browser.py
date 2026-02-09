import webbrowser


class BrowserSkill:
    def __init__(self):
        self.enabled = True
        print("Browser Skill: Enabled")

    def open_url(self, url, desktop_manager=None):
        if not url:
            return "No URL provided"
        if not url.startswith("http"):
            url = "https://" + url
        try:
            if desktop_manager and desktop_manager.is_created:
                success = desktop_manager.launch_process(f'cmd /c start "" "{url}"')
                return f"Opened {url}" if success else f"Failed to open {url}"
            webbrowser.open(url)
            return f"Opened {url}"
        except Exception as e:
            return f"Error opening URL: {e}"

    def search(self, query, desktop_manager=None):
        if not query:
            return "No query provided"
        try:
            if "." in query and " " not in query:
                return self.open_url(query, desktop_manager=desktop_manager)

            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            if desktop_manager and desktop_manager.is_created:
                success = desktop_manager.launch_process(f'cmd /c start "" "{url}"')
                return f"Searched Google for '{query}'" if success else f"Failed to search"
            webbrowser.open(url)
            return f"Searched Google for '{query}'"
        except Exception as e:
            return f"Error searching: {e}"
