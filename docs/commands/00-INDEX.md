# Komendy głosowe Jessiki — INDEX

**Lista komend jest ZAMKNIĘTA** (decyzja 2026-09-08): Jessica wykonuje
wyłącznie to, co tu opisano. Niedopasowana wypowiedź → sygnał „nie
zrozumiałam”; rozmowę z LLM otwiera wyłącznie komenda `ai_query`
(„sprawdź …”). Plik generowany przez `scripts/gen-command-docs.py` —
opisy edytuj w mapach DESC/ENGINE_COMMANDS tego skryptu.

Komend: **95**.

| Komenda | Opis |
|---|---|
| [`add_reminder`](add_reminder.md) | Akcja: `tool_call`. |
| [`ai_query`](ai_query.md) | Jedyne wejście do rozmowy z LLM (zamknięta lista komend). |
| [`apply_change`](apply_change.md) | Akcja: `confirm_loud`. |
| [`audiobook_play`](audiobook_play.md) | Akcja: `tool_call`. |
| [`cancel_change`](cancel_change.md) | Akcja: `cancel_pending`. |
| [`chapter_next`](chapter_next.md) | Akcja: `media_transport`. |
| [`chapter_prev`](chapter_prev.md) | Akcja: `media_transport`. |
| [`cloud_kill_switch`](cloud_kill_switch.md) | Akcja: `mutate`. |
| [`cloud_re_enable`](cloud_re_enable.md) | Akcja: `mutate`. |
| [`daily_briefing`](daily_briefing.md) | Akcja: `tool_call`. |
| [`date_query`](date_query.md) | Mówi dzisiejszą datę (lokalny zegar, bez LLM). |
| [`detect_language`](detect_language.md) | Akcja: `unpin_language`. |
| [`disable_ssh`](disable_ssh.md) | Wyłącza dostęp SSH. |
| [`email_list_unread`](email_list_unread.md) | Akcja: `tool_call`. |
| [`email_reply`](email_reply.md) | Akcja: `enter_dictation`. |
| [`enable_ssh`](enable_ssh.md) | Włącza dostęp SSH (ścieżka ratunkowa). |
| [`fabric_join_existing`](fabric_join_existing.md) | Akcja: `tool_call`. |
| [`fabric_kick_peer`](fabric_kick_peer.md) | Akcja: `tool_call`. |
| [`fabric_leave`](fabric_leave.md) | Akcja: `tool_call`. |
| [`fabric_pair_new_device`](fabric_pair_new_device.md) | Akcja: `tool_call`. |
| [`fabric_route_buds`](fabric_route_buds.md) | Akcja: `tool_call`. |
| [`fabric_route_speaker`](fabric_route_speaker.md) | Akcja: `tool_call`. |
| [`fabric_share_internet`](fabric_share_internet.md) | Akcja: `tool_call`. |
| [`fabric_show_on_tablet`](fabric_show_on_tablet.md) | Akcja: `tool_call`. |
| [`fabric_stop_share_internet`](fabric_stop_share_internet.md) | Akcja: `tool_call`. |
| [`fabric_use_local_brain`](fabric_use_local_brain.md) | Akcja: `mutate`. |
| [`fabric_use_pi_brain`](fabric_use_pi_brain.md) | Akcja: `mutate`. |
| [`facebook_add_friend`](facebook_add_friend.md) | Akcja: `tool_call`. |
| [`facebook_feed`](facebook_feed.md) | Akcja: `tool_call`. |
| [`facebook_messenger`](facebook_messenger.md) | Akcja: `tool_call`. |
| [`gemini_ask`](gemini_ask.md) | Akcja: `tool_call`. |
| [`gemini_deep_research`](gemini_deep_research.md) | Akcja: `tool_call`. |
| [`go_to_sleep`](go_to_sleep.md) | Usypia nasłuch komend (wake word nadal działa). |
| [`library_search`](library_search.md) | Akcja: `tool_call`. |
| [`list_audiobooks`](list_audiobooks.md) | Akcja: `tool_call`. |
| [`list_commands`](list_commands.md) | Akcja: `tool_call`. |
| [`list_languages`](list_languages.md) | Akcja: `tool_call`. |
| [`list_notes`](list_notes.md) | Akcja: `tool_call`. |
| [`list_reminders`](list_reminders.md) | Akcja: `tool_call`. |
| [`media_pause`](media_pause.md) | Akcja: `media_transport`. |
| [`media_play`](media_play.md) | Akcja: `tool_call`. |
| [`media_resume`](media_resume.md) | Akcja: `media_transport`. |
| [`memory_count`](memory_count.md) | Akcja: `tool_call`. |
| [`memory_delete_last`](memory_delete_last.md) | Akcja: `tool_call`. |
| [`memory_search`](memory_search.md) | Akcja: `tool_call`. |
| [`music_next`](music_next.md) | Akcja: `tool_call`. |
| [`music_now_playing`](music_now_playing.md) | Akcja: `tool_call`. |
| [`music_play`](music_play.md) | Akcja: `tool_call`. |
| [`music_prev`](music_prev.md) | Akcja: `tool_call`. |
| [`music_shuffle`](music_shuffle.md) | Akcja: `tool_call`. |
| [`name_set_get`](name_set_get.md) | Ustawia/odczytuje imię właściciela (profil). |
| [`news_brief`](news_brief.md) | Akcja: `tool_call`. |
| [`news_briefing`](news_briefing.md) | Czyta skrót wiadomości ze skonfigurowanych źródeł. |
| [`note_create`](note_create.md) | Akcja: `tool_call`. |
| [`note_recall`](note_recall.md) | Akcja: `tool_call`. |
| [`privacy_what_do_you_know`](privacy_what_do_you_know.md) | Akcja: `tool_call`. |
| [`radio_by_name`](radio_by_name.md) | Gra stację radiową. |
| [`radio_play`](radio_play.md) | Akcja: `tool_call`. |
| [`radio_stop`](radio_stop.md) | Akcja: `tool_call`. |
| [`rain_forecast`](rain_forecast.md) | Akcja: `tool_call`. |
| [`reboot`](reboot.md) | Restart urządzenia (z potwierdzeniem). |
| [`recall_notes`](recall_notes.md) | Wyszukiwanie w pamięci (notatki + memo głosowe). |
| [`recommend`](recommend.md) | Rekomendacja książki/muzyki z lokalnej biblioteki. |
| [`register_casual`](register_casual.md) | Akcja: `mutate`. |
| [`register_formal`](register_formal.md) | Akcja: `mutate`. |
| [`remember_note`](remember_note.md) | Akcja: `tool_call`. |
| [`remember_note`](remember_note.md) | Zapisuje notatkę (tekst + audio wypowiedzi, gdy klip jest świeży). |
| [`reminder_create`](reminder_create.md) | Akcja: `tool_call`. |
| [`reminder_delete`](reminder_delete.md) | Akcja: `tool_call`. |
| [`reminders`](reminders.md) | Ustawia i wylicza przypomnienia. |
| [`repeat_last`](repeat_last.md) | Powtarza ostatnią odpowiedź. |
| [`set_name`](set_name.md) | Akcja: `tool_call`. |
| [`shutdown`](shutdown.md) | Wyłączenie urządzenia (z potwierdzeniem). |
| [`site_read`](site_read.md) | Pobiera i streszcza stronę WWW. |
| [`sport_brief`](sport_brief.md) | Akcja: `tool_call`. |
| [`stop_talking`](stop_talking.md) | Natychmiast przerywa wypowiedź/przetwarzanie. |
| [`switch_language`](switch_language.md) | Przełącza język (runtime: polski). |
| [`switch_voice`](switch_voice.md) | Zmienia głos TTS. |
| [`time_query`](time_query.md) | Mówi aktualny czas (lokalny zegar, bez LLM). |
| [`voice_learn`](voice_learn.md) | Uczy się głosu właściciela (5 próbek + imię). |
| [`voice_memo_play`](voice_memo_play.md) | Akcja: `tool_call`. |
| [`voice_memo_play_last`](voice_memo_play_last.md) | Akcja: `tool_call`. |
| [`voice_memo_record`](voice_memo_record.md) | Akcja: `tool_call`. |
| [`voice_note`](voice_note.md) | Dyktowanie i odsłuch notatek głosowych. |
| [`volume_down`](volume_down.md) | Ciszej o 30 pp (media; nigdy poniżej 10%). |
| [`volume_set`](volume_set.md) | Ustawia głośność na wskazany procent. |
| [`volume_up`](volume_up.md) | Głośniej o 30 pp (media; nigdy poniżej 10%). |
| [`wake_ack`](wake_ack.md) | Krótkie potwierdzenie obecności po wake wordzie. |
| [`wake_up`](wake_up.md) | Przywraca nasłuch. |
| [`weather`](weather.md) | Prognoza pogody. |
| [`weather_query`](weather_query.md) | Akcja: `tool_call`. |
| [`web_read_page`](web_read_page.md) | Akcja: `tool_call`. |
| [`what_can_you_do`](what_can_you_do.md) | Akcja: `say`. |
| [`what_date`](what_date.md) | Akcja: `tool_call`. |
| [`what_time`](what_time.md) | Akcja: `tool_call`. |
