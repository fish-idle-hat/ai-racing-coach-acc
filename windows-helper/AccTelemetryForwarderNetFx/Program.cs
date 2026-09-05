using System;
using System.Globalization;
using System.IO.MemoryMappedFiles;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace AccTelemetryForwarderNetFx
{
    internal static class Program
    {
        private const long GraphicsAccCarCoordinatesOffset = 176;
        private const long GraphicsAccCarIdOffset = GraphicsAccCarCoordinatesOffset + (60 * 3 * 4);
        private const long GraphicsAccPlayerCarIdOffset = GraphicsAccCarIdOffset + (60 * 4);
        private const long GraphicsAccWideIsValidLapOffset = 1408;
        private const long GraphicsAccAnsiIsValidLapOffset = 1376;
        private const long GraphicsLegacyTyreCompoundOffset = 176;
        private const long GraphicsLegacyReplayTimeMultiplierOffset = GraphicsLegacyTyreCompoundOffset + (33 * 2) + 2;
        private const long GraphicsLegacyNormalizedCarPositionOffset = GraphicsLegacyReplayTimeMultiplierOffset + 4;
        private const long GraphicsLegacyCarCoordinatesOffset = GraphicsLegacyNormalizedCarPositionOffset + 4;
        private const long GraphicsLegacyCarIdOffset = GraphicsLegacyCarCoordinatesOffset + (60 * 3 * 4);
        private const long GraphicsLegacyPlayerCarIdOffset = GraphicsLegacyCarIdOffset + (60 * 4);
        private const long PhysicsTyreCoreTemperatureOffset = 152;
        private const long PhysicsWheelSlipOffset = 56;
        private const long PhysicsAccGOffset = 44;
        private const long PhysicsHeadingOffset = 208;
        private const long PhysicsPitchOffset = 212;
        private const long PhysicsRollOffset = 216;
        private const long PhysicsCarDamageOffset = 224;
        private const long PhysicsNumberOfTyresOutOffset = 244;
        private const long PhysicsPitLimiterOnOffset = 248;
        private const long PhysicsAbsOffset = 252;
        private const long PhysicsAirTempOffset = 288;
        private const long PhysicsRoadTempOffset = 292;
        private const long PhysicsLocalAngularVelOffset = 296;
        private const int CrewChiefVehicleCount = 64;
        private const int CrewChiefHeaderSize = 520;
        private const int CrewChiefVehicleSize = 228;
        private const int CrewChiefVehicleCarIdOffset = 0;
        private const int CrewChiefVehicleCurrentLapInvalidOffset = 144;

        private static readonly string[] PhysicsMapNames = {
            "Local\\acpmf_physics",
            "acpmf_physics"
        };

        private static readonly string[] GraphicsMapNames = {
            "Local\\acpmf_graphics",
            "acpmf_graphics"
        };

        private static readonly string[] StaticMapNames = {
            "Local\\acpmf_static",
            "acpmf_static"
        };

        private static readonly string[] CrewChiefMapNames = {
            "Local\\acpmf_crewchief",
            "acpmf_crewchief"
        };

        private static int Main(string[] args)
        {
            string host = args.Length >= 1 ? args[0] : "127.0.0.1";
            int port = args.Length >= 2 ? ParseInt(args[1], 47777) : 47777;
            double hz = args.Length >= 3 ? ParseDouble(args[2], 30.0) : 30.0;
            int intervalMs = Math.Max(5, (int)Math.Round(1000.0 / hz));

            Console.WriteLine("ACC Telemetry Forwarder - .NET Framework build");
            Console.WriteLine("Target: udp://" + host + ":" + port + " at ~" + hz.ToString("0.##", CultureInfo.InvariantCulture) + " Hz");
            Console.WriteLine("This helper reads ACC physics plus lap/session metadata when available.");
            Console.WriteLine("Start ACC, enter a driving session, then run this helper.");
            Console.WriteLine("Stop with Ctrl+C.");

            MemoryMappedFile physicsMap = null;
            MemoryMappedFile graphicsMap = null;
            MemoryMappedFile staticMap = null;
            MemoryMappedFile crewChiefMap = null;
            string connectedName = "";

            while (physicsMap == null)
            {
                foreach (string name in PhysicsMapNames)
                {
                    try
                    {
                        physicsMap = MemoryMappedFile.OpenExisting(name, MemoryMappedFileRights.Read);
                        connectedName = name;
                        break;
                    }
                    catch
                    {
                    }
                }

                if (physicsMap == null)
                {
                    Console.WriteLine("Waiting for ACC physics shared memory...");
                    Thread.Sleep(1000);
                }
            }

            Console.WriteLine("Connected to " + connectedName);

            using (UdpClient udp = new UdpClient())
            {
                IPEndPoint endpoint = new IPEndPoint(IPAddress.Parse(host), port);
                long sent = 0;
                DateTime lastStatus = DateTime.UtcNow;
                DateTime lastMapProbe = DateTime.MinValue;
                MemoryMappedViewAccessor physicsView = physicsMap.CreateViewAccessor(0, 512, MemoryMappedFileAccess.Read);
                MemoryMappedViewAccessor graphicsView = null;
                MemoryMappedViewAccessor staticView = null;
                MemoryMappedViewAccessor crewChiefView = null;
                string graphicsName = "";
                string staticName = "";
                string crewChiefName = "";
                int lastPhysicsPacketId = Int32.MinValue;
                int lastGraphicsPacketId = Int32.MinValue;
                int stalePhysicsSamples = 0;

                while (true)
                {
                    if ((DateTime.UtcNow - lastMapProbe).TotalSeconds >= 1)
                    {
                        if (graphicsMap == null)
                        {
                            graphicsMap = TryOpenFirst(GraphicsMapNames, out graphicsName);
                            if (graphicsMap != null)
                            {
                                graphicsView = graphicsMap.CreateViewAccessor(0, 4096, MemoryMappedFileAccess.Read);
                                Console.WriteLine("Connected to " + graphicsName);
                            }
                        }

                        if (staticMap == null)
                        {
                            staticMap = TryOpenFirst(StaticMapNames, out staticName);
                            if (staticMap != null)
                            {
                                staticView = staticMap.CreateViewAccessor(0, 4096, MemoryMappedFileAccess.Read);
                                Console.WriteLine("Connected to " + staticName);
                            }
                        }

                        if (crewChiefMap == null)
                        {
                            crewChiefMap = TryOpenFirst(CrewChiefMapNames, out crewChiefName);
                            if (crewChiefMap != null)
                            {
                                crewChiefView = crewChiefMap.CreateViewAccessor(0, 32768, MemoryMappedFileAccess.Read);
                                Console.WriteLine("Connected to " + crewChiefName);
                            }
                        }

                        lastMapProbe = DateTime.UtcNow;
                    }

                    int packetId = ReadInt32(physicsView, 0);
                    float throttle = ReadFloat(physicsView, 4);
                    float brake = ReadFloat(physicsView, 8);
                    float fuel = ReadFloat(physicsView, 12);
                    int rawGear = ReadInt32(physicsView, 16);
                    int displayGear = ConvertAccGear(rawGear);
                    int rpm = ReadInt32(physicsView, 20);
                    float steer = ReadFloat(physicsView, 24);
                    float speedKmh = ReadFloat(physicsView, 28);
                    float tyreCoreTempFL = ReadFloat(physicsView, PhysicsTyreCoreTemperatureOffset);
                    float tyreCoreTempFR = ReadFloat(physicsView, PhysicsTyreCoreTemperatureOffset + 4);
                    float tyreCoreTempRL = ReadFloat(physicsView, PhysicsTyreCoreTemperatureOffset + 8);
                    float tyreCoreTempRR = ReadFloat(physicsView, PhysicsTyreCoreTemperatureOffset + 12);
                    float airTemp = ReadFloat(physicsView, PhysicsAirTempOffset);
                    float roadTemp = ReadFloat(physicsView, PhysicsRoadTempOffset);
                    float carDamageFront = ReadFloat(physicsView, PhysicsCarDamageOffset);
                    float carDamageRear = ReadFloat(physicsView, PhysicsCarDamageOffset + 4);
                    float carDamageLeft = ReadFloat(physicsView, PhysicsCarDamageOffset + 8);
                    float carDamageRight = ReadFloat(physicsView, PhysicsCarDamageOffset + 12);
                    float carDamageCenter = ReadFloat(physicsView, PhysicsCarDamageOffset + 16);

                    string json = "{"
                        + JsonPair("source", "acc_netfx_forwarder") + ","
                        + JsonPair("sent_at_utc", DateTime.UtcNow.ToString("O")) + ","
                        + JsonPair("packet_id", packetId) + ","
                        + JsonPair("speed_kmh", speedKmh) + ","
                        + JsonPair("throttle", throttle) + ","
                        + JsonPair("brake", brake) + ","
                        + JsonPair("steer", steer) + ","
                        + JsonPair("gear", displayGear) + ","
                        + JsonPair("gear_raw", rawGear) + ","
                        + JsonPair("rpm", rpm) + ","
                        + JsonPair("fuel", fuel) + ","
                        + JsonPair("tyre_core_temp_fl", tyreCoreTempFL) + ","
                        + JsonPair("tyre_core_temp_fr", tyreCoreTempFR) + ","
                        + JsonPair("tyre_core_temp_rl", tyreCoreTempRL) + ","
                        + JsonPair("tyre_core_temp_rr", tyreCoreTempRR) + ","
                        + JsonPair("air_temp_c", airTemp) + ","
                        + JsonPair("road_temp_c", roadTemp) + ","
                        + JsonPair("wheel_slip_fl", ReadFloat(physicsView, PhysicsWheelSlipOffset)) + ","
                        + JsonPair("wheel_slip_fr", ReadFloat(physicsView, PhysicsWheelSlipOffset + 4)) + ","
                        + JsonPair("wheel_slip_rl", ReadFloat(physicsView, PhysicsWheelSlipOffset + 8)) + ","
                        + JsonPair("wheel_slip_rr", ReadFloat(physicsView, PhysicsWheelSlipOffset + 12)) + ","
                        + JsonPair("acc_g_x", ReadFloat(physicsView, PhysicsAccGOffset)) + ","
                        + JsonPair("acc_g_y", ReadFloat(physicsView, PhysicsAccGOffset + 4)) + ","
                        + JsonPair("acc_g_z", ReadFloat(physicsView, PhysicsAccGOffset + 8)) + ","
                        + JsonPair("heading", ReadFloat(physicsView, PhysicsHeadingOffset)) + ","
                        + JsonPair("pitch", ReadFloat(physicsView, PhysicsPitchOffset)) + ","
                        + JsonPair("roll", ReadFloat(physicsView, PhysicsRollOffset)) + ","
                        + JsonPair("car_damage_front", carDamageFront) + ","
                        + JsonPair("car_damage_rear", carDamageRear) + ","
                        + JsonPair("car_damage_left", carDamageLeft) + ","
                        + JsonPair("car_damage_right", carDamageRight) + ","
                        + JsonPair("car_damage_center", carDamageCenter) + ","
                        + JsonPair("car_damage_total", carDamageFront + carDamageRear + carDamageLeft + carDamageRight + carDamageCenter) + ","
                        + JsonPair("number_of_tyres_out", ReadInt32(physicsView, PhysicsNumberOfTyresOutOffset)) + ","
                        + JsonPair("pit_limiter_on", ReadInt32(physicsView, PhysicsPitLimiterOnOffset)) + ","
                        + JsonPair("abs_active", ReadFloat(physicsView, PhysicsAbsOffset)) + ","
                        + JsonPair("local_angular_vel_x", ReadFloat(physicsView, PhysicsLocalAngularVelOffset)) + ","
                        + JsonPair("local_angular_vel_y", ReadFloat(physicsView, PhysicsLocalAngularVelOffset + 4)) + ","
                        + JsonPair("local_angular_vel_z", ReadFloat(physicsView, PhysicsLocalAngularVelOffset + 8));

                    int graphicsPacketId = -1;
                    if (graphicsView != null)
                    {
                        graphicsPacketId = ReadInt32(graphicsView, 0);
                        int playerCarId = ReadInt32(graphicsView, GraphicsAccPlayerCarIdOffset);
                        int playerCarIndex = FindCarIndex(graphicsView, playerCarId, GraphicsAccCarIdOffset);
                        long coordinateOffset = GraphicsAccCarCoordinatesOffset + (Math.Max(playerCarIndex, 0) * 3 * 4);
                        int legacyPlayerCarId = ReadInt32(graphicsView, GraphicsLegacyPlayerCarIdOffset);
                        int legacyPlayerCarIndex = FindCarIndex(graphicsView, legacyPlayerCarId, GraphicsLegacyCarIdOffset);
                        long legacyCoordinateOffset = GraphicsLegacyCarCoordinatesOffset + (Math.Max(legacyPlayerCarIndex, 0) * 3 * 4);
                        float accRawX = ReadFloat(graphicsView, coordinateOffset);
                        float accRawY = ReadFloat(graphicsView, coordinateOffset + 4);
                        float accRawZ = ReadFloat(graphicsView, coordinateOffset + 8);
                        float legacyRawX = ReadFloat(graphicsView, legacyCoordinateOffset);
                        float legacyRawY = ReadFloat(graphicsView, legacyCoordinateOffset + 4);
                        float legacyRawZ = ReadFloat(graphicsView, legacyCoordinateOffset + 8);

                        json += ","
                            + JsonPair("graphics_packet_id", graphicsPacketId) + ","
                            + JsonPair("session_status", ReadInt32(graphicsView, 4)) + ","
                            + JsonPair("session_type", ReadInt32(graphicsView, 8)) + ","
                            + JsonPair("current_lap_display", ReadWideString(graphicsView, 12, 15)) + ","
                            + JsonPair("last_lap_display", ReadWideString(graphicsView, 42, 15)) + ","
                            + JsonPair("best_lap_display", ReadWideString(graphicsView, 72, 15)) + ","
                            + JsonPair("split_display", ReadWideString(graphicsView, 102, 15)) + ","
                            + JsonPair("lap_count", ReadInt32(graphicsView, 132)) + ","
                            + JsonPair("position", ReadInt32(graphicsView, 136)) + ","
                            + JsonPair("lap_time_ms", ReadInt32(graphicsView, 140)) + ","
                            + JsonPair("last_lap_ms", ReadInt32(graphicsView, 144)) + ","
                            + JsonPair("best_lap_ms", ReadInt32(graphicsView, 148)) + ","
                            + JsonPair("session_time_left", ReadFloat(graphicsView, 152)) + ","
                            + JsonPair("distance_traveled", ReadFloat(graphicsView, 156)) + ","
                            + JsonPair("is_in_pit", ReadInt32(graphicsView, 160)) + ","
                            + JsonPair("current_sector_index", ReadInt32(graphicsView, 164)) + ","
                            + JsonPair("last_sector_time_ms", ReadInt32(graphicsView, 168)) + ","
                            + JsonPair("number_of_laps", ReadInt32(graphicsView, 172)) + ","
                            + JsonPair("is_valid_lap", ReadInt32Safe(graphicsView, GraphicsAccWideIsValidLapOffset, -1)) + ","
                            + JsonPair("is_valid_lap_candidate_wide", ReadInt32Safe(graphicsView, GraphicsAccWideIsValidLapOffset, -1)) + ","
                            + JsonPair("is_valid_lap_candidate_ansi", ReadInt32Safe(graphicsView, GraphicsAccAnsiIsValidLapOffset, -1)) + ","
                            + JsonPair("normalized_car_position", ReadFloat(graphicsView, GraphicsLegacyNormalizedCarPositionOffset)) + ","
                            + JsonPair("player_car_id", playerCarId) + ","
                            + JsonPair("player_car_index", playerCarIndex) + ","
                            + JsonPair("car_world_x", legacyRawY) + ","
                            + JsonPair("car_world_y", 0.0f) + ","
                            + JsonPair("car_world_z", legacyRawZ) + ","
                            + JsonPair("acc_raw_car_world_x", accRawX) + ","
                            + JsonPair("acc_raw_car_world_y", accRawY) + ","
                            + JsonPair("acc_raw_car_world_z", accRawZ) + ","
                            + JsonPair("legacy_player_car_id", legacyPlayerCarId) + ","
                            + JsonPair("legacy_player_car_index", legacyPlayerCarIndex) + ","
                            + JsonPair("legacy_car_world_x", legacyRawX) + ","
                            + JsonPair("legacy_car_world_y", legacyRawY) + ","
                            + JsonPair("legacy_car_world_z", legacyRawZ);

                        int crewInvalid = ReadCrewChiefCurrentLapInvalid(crewChiefView, playerCarId);
                        json += ","
                            + JsonPair("current_lap_invalid", crewInvalid)
                            + ","
                            + JsonPair("current_lap_valid", crewInvalid == -1 ? -1 : (crewInvalid == 0 ? 1 : 0));
                    }

                    bool physicsLooksZero = speedKmh == 0.0f
                        && throttle == 0.0f
                        && brake == 0.0f
                        && steer == 0.0f
                        && rawGear == 0
                        && rpm == 0
                        && fuel == 0.0f;
                    bool physicsPacketStuck = packetId == lastPhysicsPacketId;
                    bool graphicsPacketMoving = graphicsPacketId != -1 && graphicsPacketId != lastGraphicsPacketId;
                    if (physicsLooksZero && physicsPacketStuck && graphicsPacketMoving)
                    {
                        stalePhysicsSamples++;
                    }
                    else
                    {
                        stalePhysicsSamples = 0;
                    }

                    if (stalePhysicsSamples >= Math.Max(30, (int)hz * 3))
                    {
                        Console.WriteLine("Physics shared memory looks stale; reopening physics map.");
                        MemoryMappedFile reopenedPhysicsMap = TryOpenFirst(PhysicsMapNames, out connectedName);
                        if (reopenedPhysicsMap != null)
                        {
                            physicsView.Dispose();
                            physicsMap.Dispose();
                            physicsMap = reopenedPhysicsMap;
                            physicsView = physicsMap.CreateViewAccessor(0, 512, MemoryMappedFileAccess.Read);
                            Console.WriteLine("Reconnected to " + connectedName);
                        }
                        stalePhysicsSamples = 0;
                    }
                    lastPhysicsPacketId = packetId;
                    lastGraphicsPacketId = graphicsPacketId;

                    if (staticView != null)
                    {
                        json += ","
                            + JsonPair("shared_memory_version", ReadWideString(staticView, 0, 15)) + ","
                            + JsonPair("acc_version", ReadWideString(staticView, 30, 15)) + ","
                            + JsonPair("car_model", ReadWideString(staticView, 68, 33)) + ","
                            + JsonPair("track", ReadWideString(staticView, 134, 33)) + ","
                            + JsonPair("player_name", ReadWideString(staticView, 200, 33));
                    }

                    json += "}";

                    byte[] bytes = Encoding.UTF8.GetBytes(json);
                    udp.Send(bytes, bytes.Length, endpoint);
                    sent++;

                    if ((DateTime.UtcNow - lastStatus).TotalSeconds >= 2)
                    {
                        Console.WriteLine(
                            "sent=" + sent
                            + " speed=" + speedKmh.ToString("0.###", CultureInfo.InvariantCulture)
                            + " throttle=" + throttle.ToString("0.###", CultureInfo.InvariantCulture)
                            + " brake=" + brake.ToString("0.###", CultureInfo.InvariantCulture)
                            + " steer=" + steer.ToString("0.###", CultureInfo.InvariantCulture)
                            + " gear=" + displayGear
                            + " raw_gear=" + rawGear
                            + " rpm=" + rpm
                            + " lap=" + ReadLapDisplay(graphicsView)
                            + " pos=" + ReadPositionDisplay(graphicsView)
                            + " track=" + ReadTrack(staticView)
                        );
                        lastStatus = DateTime.UtcNow;
                    }

                    Thread.Sleep(intervalMs);
                }
            }
        }

        private static MemoryMappedFile TryOpenFirst(string[] names, out string connectedName)
        {
            foreach (string name in names)
            {
                try
                {
                    MemoryMappedFile map = MemoryMappedFile.OpenExisting(name, MemoryMappedFileRights.Read);
                    connectedName = name;
                    return map;
                }
                catch
                {
                }
            }

            connectedName = "";
            return null;
        }

        private static int ParseInt(string value, int fallback)
        {
            int parsed;
            return int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed) ? parsed : fallback;
        }

        private static double ParseDouble(string value, double fallback)
        {
            double parsed;
            return double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out parsed) ? parsed : fallback;
        }

        private static int ReadInt32(MemoryMappedViewAccessor accessor, long offset)
        {
            return accessor.ReadInt32(offset);
        }

        private static float ReadFloat(MemoryMappedViewAccessor accessor, long offset)
        {
            return accessor.ReadSingle(offset);
        }

        private static int ReadInt32Safe(MemoryMappedViewAccessor accessor, long offset, int fallback)
        {
            if (accessor == null)
            {
                return fallback;
            }

            try
            {
                return accessor.ReadInt32(offset);
            }
            catch
            {
                return fallback;
            }
        }

        private static int ReadCrewChiefCurrentLapInvalid(MemoryMappedViewAccessor crewChiefView, int playerCarId)
        {
            if (crewChiefView == null || playerCarId < 0)
            {
                return -1;
            }

            try
            {
                int vehicleCount = Math.Min(CrewChiefVehicleCount, Math.Max(0, ReadInt32(crewChiefView, 0)));
                for (int index = 0; index < vehicleCount; index++)
                {
                    int vehicleOffset = CrewChiefHeaderSize + (index * CrewChiefVehicleSize);
                    int carId = ReadInt32(crewChiefView, vehicleOffset + CrewChiefVehicleCarIdOffset);
                    if (carId == playerCarId)
                    {
                        return ReadInt32(crewChiefView, vehicleOffset + CrewChiefVehicleCurrentLapInvalidOffset);
                    }
                }
            }
            catch
            {
            }

            return -1;
        }

        private static int FindCarIndex(MemoryMappedViewAccessor graphicsView, int playerCarId, long carIdOffset)
        {
            if (graphicsView == null || playerCarId < 0)
            {
                return 0;
            }

            for (int index = 0; index < 60; index++)
            {
                int carId = ReadInt32(graphicsView, carIdOffset + (index * 4));
                if (carId == playerCarId)
                {
                    return index;
                }
            }

            return 0;
        }

        private static string ReadWideString(MemoryMappedViewAccessor accessor, long offset, int chars)
        {
            if (accessor == null)
            {
                return "";
            }

            try
            {
                byte[] bytes = new byte[chars * 2];
                accessor.ReadArray(offset, bytes, 0, bytes.Length);
                string text = Encoding.Unicode.GetString(bytes);
                int zero = text.IndexOf('\0');
                if (zero >= 0)
                {
                    text = text.Substring(0, zero);
                }

                return text.Trim();
            }
            catch
            {
                return "";
            }
        }

        private static string ReadLapDisplay(MemoryMappedViewAccessor graphicsView)
        {
            if (graphicsView == null)
            {
                return "";
            }

            return ReadWideString(graphicsView, 12, 15);
        }

        private static string ReadTrack(MemoryMappedViewAccessor staticView)
        {
            if (staticView == null)
            {
                return "";
            }

            return ReadWideString(staticView, 134, 33);
        }

        private static string ReadPositionDisplay(MemoryMappedViewAccessor graphicsView)
        {
            if (graphicsView == null)
            {
                return "";
            }

            int legacyPlayerCarId = ReadInt32(graphicsView, GraphicsLegacyPlayerCarIdOffset);
            int legacyPlayerCarIndex = FindCarIndex(graphicsView, legacyPlayerCarId, GraphicsLegacyCarIdOffset);
            long coordinateOffset = GraphicsLegacyCarCoordinatesOffset + (Math.Max(legacyPlayerCarIndex, 0) * 3 * 4);
            return ReadFloat(graphicsView, coordinateOffset + 4).ToString("0.##", CultureInfo.InvariantCulture)
                + ","
                + "0"
                + ","
                + ReadFloat(graphicsView, coordinateOffset + 8).ToString("0.##", CultureInfo.InvariantCulture);
        }

        private static int ConvertAccGear(int rawGear)
        {
            if (rawGear > 0)
            {
                return rawGear - 1;
            }

            return rawGear;
        }

        private static string JsonPair(string key, string value)
        {
            return "\"" + EscapeJson(key) + "\":\"" + EscapeJson(value) + "\"";
        }

        private static string JsonPair(string key, int value)
        {
            return "\"" + EscapeJson(key) + "\":" + value.ToString(CultureInfo.InvariantCulture);
        }

        private static string JsonPair(string key, float value)
        {
            return "\"" + EscapeJson(key) + "\":" + value.ToString("R", CultureInfo.InvariantCulture);
        }

        private static string EscapeJson(string value)
        {
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }
    }
}
