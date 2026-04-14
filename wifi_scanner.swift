import CoreWLAN
import Foundation

struct NetworkInfo: Codable {
    let ssid: String
    let bssid: String
    let rssi: Int
    let noise: Int
    let channel: Int
    let channelBand: Int
    let channelWidth: Int
    let hidden: Bool
    let beaconInterval: Int
}

struct ScanResult: Codable {
    let timestamp: String
    let networks: [NetworkInfo]
    let totalCount: Int
    let hiddenCount: Int
}

let formatter = DateFormatter()
formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
let timestamp = formatter.string(from: Date())

let client = CWWiFiClient.shared()
guard let iface = client.interface() else {
    let errorResult: [String: String] = ["error": "No WiFi interface found"]
    let data = try! JSONEncoder().encode(errorResult)
    print(String(data: data, encoding: .utf8)!)
    exit(1)
}

do {
    let scannedNetworks = try iface.scanForNetworks(withName: nil)

    var networks: [NetworkInfo] = []
    for network in scannedNetworks {
        let info = NetworkInfo(
            ssid: network.ssid ?? "",
            bssid: network.bssid ?? "",
            rssi: network.rssiValue,
            noise: network.noiseMeasurement,
            channel: network.wlanChannel?.channelNumber ?? 0,
            channelBand: Int(network.wlanChannel?.channelBand.rawValue ?? 0),
            channelWidth: Int(network.wlanChannel?.channelWidth.rawValue ?? 0),
            hidden: (network.ssid == nil || network.ssid == ""),
            beaconInterval: network.beaconInterval
        )
        networks.append(info)
    }

    let hiddenCount = networks.filter { $0.hidden }.count
    let result = ScanResult(
        timestamp: timestamp,
        networks: networks,
        totalCount: networks.count,
        hiddenCount: hiddenCount
    )

    let encoder = JSONEncoder()
    encoder.outputFormatting = .prettyPrinted
    let data = try encoder.encode(result)
    print(String(data: data, encoding: .utf8)!)
} catch {
    let errorResult: [String: String] = ["error": "Scan failed: \(error.localizedDescription)"]
    let data = try! JSONEncoder().encode(errorResult)
    print(String(data: data, encoding: .utf8)!)
    exit(1)
}
