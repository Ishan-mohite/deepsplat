import XCTest
import DeepSplatCore
@testable import DeepSplatCapture

private final class HTTPStub: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (status, data) = try Self.handler!(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

final class AppTests: XCTestCase {
    func testMockUploadIsAnIdempotentCopyOfArchiveBytes() throws {
        let id = UUID()
        let archive = FileManager.default.temporaryDirectory.appendingPathComponent(id.uuidString + ".zip")
        let output = CapturePaths.mockUploads.appendingPathComponent(id.uuidString + ".zip")
        defer { try? FileManager.default.removeItem(at: archive); try? FileManager.default.removeItem(at: output) }
        let data = Data([0x50, 0x4b, 1, 2, 3]); try data.write(to: archive)
        try MockBackendClient().upload(id: id, archive: archive)
        try MockBackendClient().upload(id: id, archive: archive)
        XCTAssertEqual(try Data(contentsOf: output), data)
    }
    func testBackendConfirmationUsesTokenAndStableCaptureID() async throws {
        let configuration = URLSessionConfiguration.ephemeral; configuration.protocolClasses = [HTTPStub.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel(); HTTPStub.handler = nil }
        let id = UUID()
        HTTPStub.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/api/v1/captures/\(id.uuidString)/complete")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-only-token")
            return (202, Data(#"{"status":"processing"}"#.utf8))
        }
        let client = BackendClient(base: URL(string: "https://example.com/api")!, token: "test-only-token", transport: session)
        let response = try await client.complete(id)
        XCTAssertEqual(response.status, .processing)
    }
    func testUnauthorizedResponseRequiresTokenUpdate() async throws {
        let configuration = URLSessionConfiguration.ephemeral; configuration.protocolClasses = [HTTPStub.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel(); HTTPStub.handler = nil }
        HTTPStub.handler = { _ in (401, Data()) }
        let client = BackendClient(base: URL(string: "https://example.com")!, token: "rejected-test-token", transport: session)
        do { _ = try await client.complete(UUID()); XCTFail("Accepted rejected token") }
        catch { XCTAssertTrue(error.localizedDescription.contains("401")) }
    }
}
