package dev.tcg.scanner
import org.junit.Assert.*
import org.junit.Test
class LanAddressTest {
    @Test fun acceptsOnlyPrivateNumericAddresses() {
        assertEquals("192.168.1.23" to 8765,LanAddress.parse("192.168.1.23"))
        assertEquals("10.0.2.2" to 9000,LanAddress.parse("10.0.2.2:9000"))
        for(value in listOf("8.8.8.8","0.0.0.0","127.0.0.1","169.254.1.2","100.64.0.1","172.32.0.1","example.com","https://192.168.1.2","192.168.1.2:0","10.0.0.256","192.168.1.2:8765/path")) {
            assertTrue(value,runCatching { LanAddress.parse(value) }.isFailure)
        }
    }
}
