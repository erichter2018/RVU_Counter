# Gemini Ideas: The Future of RVU Counter

This document contains a curated list of ideas for evolving the RVU Counter, ranging from simple quality-of-life improvements to experimental "moonshot" features.

---

## 🟢 Minimal & Quality of Life (Low Effort, High Value)

1.  **Customizable Themes (Dark/Light/System)**: Allow users to choose specific accent colors or fully custom themes beyond just a "dark mode" toggle.
2.  **Global Hotkeys**: Configurable keyboard shortcuts to toggle window visibility, start/end shifts, or manually add a study without having to click the UI.
3.  **Enhanced Tooltips**: Hovering over statistics (like "Projected") could show the exact formula used for transparency.
4.  **Audio Cues**: Optional, subtle "ding" or "click" sound when a study is successfully detected and recorded.
5.  **Recent Study "Undo"**: A small toast notification or button to quickly delete the most recently added study if it was a false positive.
6.  **Compact Mode**: A "super-minimal" UI mode that only shows Total RVUs and Last Hour in a tiny, always-on-top strip.

---

## 🔵 Logical & Evolutionary (Medium Effort, Structural)

7.  **Advanced Filtering/Search**: In the Statistics window, add the ability to search by Accession number or filter history by specific modalities (e.g., "Show only MRI shifts").
8.  **Multi-User / Group Profiles**: Support for multiple users on the same machine with password-protected profiles or simple local switching.
9.  **Rich Export Formats**: Export shift data directly to professionally formatted PDF reports or Excel spreadsheets with pre-built charts.
10. **Break/Distraction Tracking**: A button to mark "on break" or "meeting," which pauses the pace car and efficiency calculations to avoid penalizing the user for non-clinical time.
11. **Auto-Update System**: A mechanism to notify users of new versions and download them automatically.
12. **Modality Goals**: Set specific RVU or count targets for different modalities (e.g., "Goal: 5 MRIs today").

---

## 🟣 Creative & Ambitious (High Effort, High Tech)

13. **Local LLM Classification**: Use a tiny, local LLM (like Phi-3 or similar) to handle complex study descriptions that don't fit simple keyword rules, allowing for "natural language" matching.
14. **Predictive Analytics**: Analyze historical shift data to predict "lulls" or "surges" in study volume throughout the day based on the user's specific history.
15. **Burnout Prevention / Wellness Engine**: If the "RVU Delta" is consistently extremely high for too long, suggest a 5-minute stretch or coffee break to maintain long-term focus.
16. **Voice Assistant Integration**: "Hey RVU Counter, what's my current pace?" or "Add a 1.5 RVU misc study."
17. **Smart Prior Fetching**: Automatically detect when a new study appears and use pywinauto to trigger the "Get Prior" routine in InteleViewer/PowerScribe before the user even clicks it.
18. **Mobile Companion App**: A read-only companion app (via a secure local relay or cloud backup) so you can check your shift progress on your phone during a lunch break.

---

## 🔴 The "Crazy" & Experimental (Experimental/Fun)

19. **Gamification & RPG Elements**: Earn XP and "level up" your rank (e.g., "RVU Novice" -> "RVU Grandmaster"). Unlock digital badges for "100 RVU Shift" or "Monday Morning Warrior."
20. **Productivity Soundtrack**: An integrated music player that adjusts the BPM/tempo of a lo-fi or focus playlist based on your current "Pace Car" status (faster music if you're behind).
21. **Wearable Haptics**: Vibrate a smartwatch (via API) whenever a study is completed so you get physical feedback without looking at the counter.
22. **Ghost Shift Comparison**: Display a "ghost" line on the RVU progression graph representing your "Personal Best" shift at this same time of day for competition against yourself.
23. **AR/HUD Overlay**: If using AR glasses (like Xreal), project the RVU Counter into your peripheral vision so it doesn't take up any screen real estate at all.
24. **AI-Generated Shift Names**: Use an LLM to give your shifts funny, creative names based on the content (e.g., "The MRI Marathon" or "The Great CT Surge of Tuesday").

---

## 🚀 Implementation Strategy

- **Phase 1**: Focus on "Minimal" items (Themes, Hotkeys) to polish the experience.
- **Phase 2**: Implement "Logical" enhancements (Advanced Filtering, Rich Exports) to increase utility for heavy users.
- **Phase 3**: Experiment with "Creative" features (Local LLM, Predictive Analytics) as "Pro" version additions.
