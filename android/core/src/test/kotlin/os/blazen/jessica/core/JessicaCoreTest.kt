package os.blazen.jessica.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class JessicaCoreTest {

    private val yaml = """
        version: 1
        intents:
          - name: volume_up
            triggers:
              en: ["louder"]
              pl: ["g(ł|l)o(ś|s)niej"]
            action: mutate
          - name: time_query
            triggers:
              en: ["what time is it"]
              pl: ["która godzina"]
            action: query
          - name: language_pin_en
            triggers:
              en: ["speak english"]
              pl: ["m(ó|o)w po angielsku"]
            action: mutate
          - name: stop
            triggers:
              en: ["stop"]
              pl: ["stop", "cisza"]
            action: mutate
    """.trimIndent()

    @Test
    fun `loads catalogue and exposes count`() {
        val core = JessicaCore.create()
        assertTrue(core.loadIntents(yaml), "loadIntents returned false")
        assertEquals(4L, core.intentCount())
        core.close()
    }

    @Test
    fun `matches polish trigger to the right intent`() {
        val core = JessicaCore.create().apply { loadIntents(yaml) }
        val m = core.matchIntent("głośniej", "pl")
        assertNotNull(m)
        assertEquals("volume_up", m!!.name)
        assertEquals("pl", m.language)
        assertEquals("mutate", m.action)
        core.close()
    }

    @Test
    fun `matches english trigger`() {
        val core = JessicaCore.create().apply { loadIntents(yaml) }
        val m = core.matchIntent("Louder, please", "en")
        assertNotNull(m)
        assertEquals("volume_up", m!!.name)
        core.close()
    }

    @Test
    fun `polish language pin intent matches`() {
        val core = JessicaCore.create().apply { loadIntents(yaml) }
        val m = core.matchIntent("mów po angielsku", "pl")
        assertNotNull(m)
        assertEquals("language_pin_en", m!!.name)
        core.close()
    }

    @Test
    fun `english language pin intent matches`() {
        val core = JessicaCore.create().apply { loadIntents(yaml) }
        val m = core.matchIntent("Speak English please", "en")
        assertNotNull(m)
        assertEquals("language_pin_en", m!!.name)
        core.close()
    }

    @Test
    fun `stop intent matches both languages`() {
        val core = JessicaCore.create().apply { loadIntents(yaml) }
        assertNotNull(core.matchIntent("Stop", "en"))
        assertNotNull(core.matchIntent("cisza", "pl"))
        core.close()
    }

    @Test
    fun `returns null when nothing matches`() {
        val core = JessicaCore.create().apply { loadIntents(yaml) }
        assertNull(core.matchIntent("powiedz coś mądrego", "pl"))
        core.close()
    }
}
