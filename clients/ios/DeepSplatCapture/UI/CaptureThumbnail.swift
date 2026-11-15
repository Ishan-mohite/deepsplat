import SwiftUI
import ImageIO

struct CaptureThumbnail: View {
    let id: UUID
    @State private var image: UIImage?
    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Design.elevated
                if let image {
                    Image(uiImage: image).resizable().scaledToFill()
                        .frame(width: geometry.size.width, height: geometry.size.height).clipped()
                } else {
                    Image(systemName: "camera").font(.title3.weight(.light)).foregroundStyle(Design.blue)
                }
            }.frame(width: geometry.size.width, height: geometry.size.height)
        }
        .accessibilityHidden(true)
        .task(id: id) {
            let url = CapturePaths.folder(id).appendingPathComponent("photo.jpg")
            image = await Task.detached(priority: .utility) { () -> UIImage? in
                guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
                      let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                        kCGImageSourceCreateThumbnailFromImageAlways: true,
                        kCGImageSourceThumbnailMaxPixelSize: 240
                      ] as CFDictionary) else { return nil }
                return UIImage(cgImage: thumbnail, scale: 1, orientation: .right)
            }.value
        }
    }
}
