import SwiftUI

/// Native equivalents of design/system/tokens.json. Typography uses the system-ui
/// fallbacks declared there, with Dynamic Type scaling and no network font loading.
enum Design {
    static let background = Color(hex: 0xF1EEE6)
    static let elevated = Color(hex: 0xE8E2D4)
    static let surface = Color(hex: 0xFBFAF7)
    static let ink = Color(hex: 0x172033)
    static let muted = Color(hex: 0x4A5163)
    static let hint = Color(hex: 0x7D786A)
    static let line = Color(hex: 0xE2DCCF)
    static let controlLine = Color(hex: 0xD3CBBB)
    static let blue = Color(hex: 0x2453D4)
    static let blueSoft = Color(hex: 0xE3E9FB)
    static let green = Color(hex: 0x2F7D4B)
    static let greenSoft = Color(hex: 0xE1EFE3)
    static let amber = Color(hex: 0x946300)
    static let amberSoft = Color(hex: 0xF6ECD2)
    static let rust = Color(hex: 0xC24A1C)
    static let viewport = Color(hex: 0x1A2130)
    static let viewportSurface = Color(hex: 0x232C3E)
    static let viewportLine = Color(hex: 0x36435C)
    static let viewportInk = Color(hex: 0xE9ECF3)
    static let viewportMuted = Color(hex: 0x9AA4B8)
    static let pick = Color(hex: 0x7EA2FF)
}

private extension Color {
    init(hex: UInt32) {
        self.init(.sRGB, red: Double((hex >> 16) & 255) / 255,
                  green: Double((hex >> 8) & 255) / 255, blue: Double(hex & 255) / 255, opacity: 1)
    }
}

struct PrimaryStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.subheadline.weight(.semibold))
            .frame(maxWidth: .infinity, minHeight: 24)
            .padding(.horizontal, 20).padding(.vertical, 12)
            .foregroundStyle(.white).background(Design.blue, in: Capsule())
            .opacity(isEnabled ? (configuration.isPressed ? 0.8 : 1) : 0.45)
    }
}

struct SecondaryStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.subheadline.weight(.semibold))
            .frame(maxWidth: .infinity, minHeight: 24)
            .padding(.horizontal, 20).padding(.vertical, 12)
            .foregroundStyle(Design.ink).background(configuration.isPressed ? Design.elevated : Design.surface, in: Capsule())
            .overlay(Capsule().strokeBorder(Design.controlLine, lineWidth: 1))
            .opacity(isEnabled ? 1 : 0.45)
    }
}

struct Eyebrow: View {
    let text: String
    var body: some View {
        Text(text.uppercased()).font(.caption2.weight(.semibold))
            .tracking(1.5).foregroundStyle(Design.blue)
    }
}

struct StatusPill: View {
    let text: String
    var color: Color = Design.blue
    var symbol: String = "circle.fill"
    var body: some View {
        Label {
            Text(text).font(.caption.weight(.semibold))
        } icon: {
            Image(systemName: symbol).font(.system(size: symbol == "circle.fill" ? 6 : 11, weight: .semibold))
        }
        .foregroundStyle(color).padding(.horizontal, 12).padding(.vertical, 8)
        .background(color.opacity(0.08), in: Capsule())
        .overlay(Capsule().strokeBorder(color.opacity(0.3), lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}

struct Metric: View {
    let value: String
    let label: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(value).font(.title2.weight(.semibold)).monospacedDigit().foregroundStyle(Design.ink)
                .minimumScaleFactor(0.8)
            Text(label).font(.caption).foregroundStyle(Design.muted)
        }.frame(maxWidth: .infinity, alignment: .leading).accessibilityElement(children: .combine)
    }
}

struct CaptureStep: View {
    let number: Int
    let title: String
    var complete = false
    var active = false
    var body: some View {
        HStack(spacing: 8) {
            ZStack {
                Circle().fill(complete ? Design.greenSoft : active ? Design.blueSoft : Design.elevated)
                if complete { Image(systemName: "checkmark").font(.system(size: 10, weight: .bold)) }
                else { Text("\(number)").font(.system(.caption2, design: .monospaced).weight(.medium)) }
            }.frame(width: 24, height: 24)
            Text(title).font(.caption.weight(.medium))
        }
        .foregroundStyle(complete ? Design.green : active ? Design.blue : Design.muted)
        .accessibilityElement(children: .combine)
    }
}

/// The pick reticle from DESIGN.md, used as an aiming guide, never a localization claim.
struct PickReticle: Shape {
    func path(in rect: CGRect) -> Path {
        let c = CGPoint(x: rect.midX, y: rect.midY)
        let r = min(rect.width, rect.height) * 0.33
        var path = Path(ellipseIn: CGRect(x: c.x-r, y: c.y-r, width: 2*r, height: 2*r))
        for direction in [CGVector(dx: 1, dy: 0), CGVector(dx: -1, dy: 0), CGVector(dx: 0, dy: 1), CGVector(dx: 0, dy: -1)] {
            path.move(to: CGPoint(x: c.x+direction.dx*(r-6), y: c.y+direction.dy*(r-6)))
            path.addLine(to: CGPoint(x: c.x+direction.dx*(r+12), y: c.y+direction.dy*(r+12)))
        }
        path.addEllipse(in: CGRect(x: c.x-2, y: c.y-2, width: 4, height: 4))
        return path
    }
}

extension View {
    func captureCard() -> some View {
        padding(20).frame(maxWidth: .infinity, alignment: .leading)
            .background(Design.surface, in: RoundedRectangle(cornerRadius: 22))
            .overlay(RoundedRectangle(cornerRadius: 22).strokeBorder(Design.line, lineWidth: 1))
    }
}
