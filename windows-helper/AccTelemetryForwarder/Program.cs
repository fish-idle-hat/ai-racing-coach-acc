using System.IO.MemoryMappedFiles;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

const string PhysicsMap = "Local\\acpmf_physics";
const string GraphicsMap = "Local\\acpmf_graphics";
const string StaticMap = "Local\\acpmf_static";

var host = args.Length >= 1 ? args[0] : "127.0.0.1";
var port = args.Length >= 2 && int.TryParse(args[1], out var parsedPort) ? parsedPort : 47777;
var hz = args.Length >= 3 && double.TryParse(args[2], out var parsedHz) ? parsedHz : 30.0;
var intervalMs = Math.Max(5, (int)Math.Round(1000.0 / hz));

using var udp = new UdpClient();
var endpoint = new IPEndPoint(IPAddress.Parse(host), port);

Console.WriteLine("ACC Telemetry Forwarder");
Console.WriteLine($"Sending to udp://{host}:{port} at ~{hz:0.##} Hz");
Console.WriteLine("Start ACC and enter a driving session before running this helper.");
Console.WriteLine("Stop with Ctrl+C.");

MemoryMappedFile? physicsMap = null;
MemoryMappedFile? graphicsMap = null;
MemoryMappedFile? staticMap = null;

while (physicsMap == null)
{
    try
    {
        physicsMap = MemoryMappedFile.OpenExisting(PhysicsMap, MemoryMappedFileRights.Read);
        Console.WriteLine($"Connected to {PhysicsMap}");
    }
    catch
    {
        Console.WriteLine($"Waiting for {PhysicsMap}...");
        Thread.Sleep(1000);
    }
}

try
{
    graphicsMap = MemoryMappedFile.OpenExisting(GraphicsMap, MemoryMappedFileRights.Read);
    Console.WriteLine($"Connected to {GraphicsMap}");
}
catch
{
    Console.WriteLine($"{GraphicsMap} not available yet; continuing with physics only.");
}

try
{
    staticMap = MemoryMappedFile.OpenExisting(StaticMap, MemoryMappedFileRights.Read);
    Console.WriteLine($"Connected to {StaticMap}");
}
catch
{
    Console.WriteLine($"{StaticMap} not available yet; continuing without static page.");
}

using var physicsView = physicsMap.CreateViewAccessor(0, 512, MemoryMappedFileAccess.Read);
using var graphicsView = graphicsMap?.CreateViewAccessor(0, 2048, MemoryMappedFileAccess.Read);
using var staticView = staticMap?.CreateViewAccessor(0, 2048, MemoryMappedFileAccess.Read);

var sent = 0L;
var lastStatus = DateTime.UtcNow;
var track = ReadWideString(staticView, 0, 33);
var carModel = ReadWideString(staticView, 66, 33);

while (true)
{
    var packet = new Dictionary<string, object?>
    {
        ["source"] = "acc_shared_memory_forwarder",
        ["sent_at_utc"] = DateTime.UtcNow.ToString("O"),
        ["packet_id"] = ReadInt32(physicsView, 0),
        ["throttle"] = ReadFloat(physicsView, 4),
        ["brake"] = ReadFloat(physicsView, 8),
        ["fuel"] = ReadFloat(physicsView, 12),
        ["gear"] = ReadInt32(physicsView, 16),
        ["rpm"] = ReadInt32(physicsView, 20),
        ["steer"] = ReadFloat(physicsView, 24),
        ["speed_kmh"] = ReadFloat(physicsView, 28),
        ["track"] = track,
        ["car_model"] = carModel,
    };

    if (graphicsView != null)
    {
        packet["graphics_packet_id"] = ReadInt32(graphicsView, 0);
        packet["status"] = ReadInt32(graphicsView, 4);
        packet["session"] = ReadInt32(graphicsView, 8);
        packet["lap_time_ms"] = ReadInt32(graphicsView, 12);
        packet["last_lap_ms"] = ReadInt32(graphicsView, 16);
        packet["best_lap_ms"] = ReadInt32(graphicsView, 20);
        packet["lap_count"] = ReadInt32(graphicsView, 24);
    }

    var json = JsonSerializer.Serialize(packet);
    var bytes = Encoding.UTF8.GetBytes(json);
    udp.Send(bytes, bytes.Length, endpoint);
    sent++;

    if ((DateTime.UtcNow - lastStatus).TotalSeconds >= 2)
    {
        Console.WriteLine($"sent={sent} speed={packet["speed_kmh"]} throttle={packet["throttle"]} brake={packet["brake"]} steer={packet["steer"]} gear={packet["gear"]} rpm={packet["rpm"]}");
        lastStatus = DateTime.UtcNow;
    }

    Thread.Sleep(intervalMs);
}

static int ReadInt32(MemoryMappedViewAccessor accessor, long offset)
{
    return accessor.ReadInt32(offset);
}

static float ReadFloat(MemoryMappedViewAccessor accessor, long offset)
{
    return accessor.ReadSingle(offset);
}

static string ReadWideString(MemoryMappedViewAccessor? accessor, long offset, int chars)
{
    if (accessor == null)
    {
        return "";
    }

    try
    {
        var bytes = new byte[chars * 2];
        accessor.ReadArray(offset, bytes, 0, bytes.Length);
        var text = Encoding.Unicode.GetString(bytes);
        var zero = text.IndexOf('\0');
        if (zero >= 0)
        {
            text = text[..zero];
        }
        return text.Trim();
    }
    catch
    {
        return "";
    }
}

