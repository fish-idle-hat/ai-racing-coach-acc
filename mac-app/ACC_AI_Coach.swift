import SwiftUI
import AppKit
import AVFoundation
import UniformTypeIdentifiers

let preferredPython = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"

struct ProjectRootResolution {
    let path: String
    let status: String
}

enum AppRuntime {
    static let legacyProjectRoot = "/Users/larryrus/Documents/ChatGPT/ACC AI COACH"
    static let projectRootOverrideKey = "projectRootOverride"

    static func resolveProjectRoot() -> ProjectRootResolution {
        let fileManager = FileManager.default
        if let saved = UserDefaults.standard.string(forKey: projectRootOverrideKey), isUsableProjectRoot(saved) {
            return ProjectRootResolution(path: saved, status: "Using connected project folder.")
        }

        guard let bundledProject = Bundle.main.resourceURL?.appendingPathComponent("AppProject"),
              fileManager.fileExists(atPath: bundledProject.appendingPathComponent("tools/realtime_coach.py").path),
              let supportBase = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            return ProjectRootResolution(path: legacyProjectRoot, status: "Using development project folder.")
        }

        let supportProject = supportBase
            .appendingPathComponent("ACC AI Coach", isDirectory: true)
            .appendingPathComponent("AppProject", isDirectory: true)

        do {
            try fileManager.createDirectory(at: supportProject, withIntermediateDirectories: true)
            try refreshProgramFolder("tools", from: bundledProject, to: supportProject)
            try refreshProgramFolder("windows-helper", from: bundledProject, to: supportProject)
            try refreshProgramFolder("build", from: bundledProject, to: supportProject)
            try copyFolderIfMissing("data", from: bundledProject, to: supportProject)
            try copyFolderIfMissing("runs", from: bundledProject, to: supportProject)
            try fileManager.createDirectory(at: supportProject.appendingPathComponent("runs"), withIntermediateDirectories: true)
            return ProjectRootResolution(path: supportProject.path, status: "Using bundled app data. Connect your project folder to restore old runs.")
        } catch {
            return ProjectRootResolution(path: bundledProject.path, status: "Using read-only bundled app data.")
        }
    }

    static func isUsableProjectRoot(_ path: String) -> Bool {
        let root = URL(fileURLWithPath: path)
        return FileManager.default.fileExists(atPath: root.appendingPathComponent("tools/realtime_coach.py").path)
    }

    private static func refreshProgramFolder(_ name: String, from sourceRoot: URL, to targetRoot: URL) throws {
        let fileManager = FileManager.default
        let source = sourceRoot.appendingPathComponent(name)
        guard fileManager.fileExists(atPath: source.path) else { return }
        let target = targetRoot.appendingPathComponent(name)
        if fileManager.fileExists(atPath: target.path) {
            try fileManager.removeItem(at: target)
        }
        try fileManager.copyItem(at: source, to: target)
    }

    private static func copyFolderIfMissing(_ name: String, from sourceRoot: URL, to targetRoot: URL) throws {
        let fileManager = FileManager.default
        let source = sourceRoot.appendingPathComponent(name)
        let target = targetRoot.appendingPathComponent(name)
        guard fileManager.fileExists(atPath: source.path),
              !fileManager.fileExists(atPath: target.path) else { return }
        try fileManager.copyItem(at: source, to: target)
    }
}

enum RachelVoice {
    private static let presentationVoiceIdentifier = "com.apple.voice.compact.en-US.Samantha"
    static let liveRate: Float = 0.54
    static let tutorialRate: Float = 0.50

    static func makeSynthesizer() -> AVSpeechSynthesizer {
        AVSpeechSynthesizer()
    }

    static func utterance(_ text: String, rate: Float) -> AVSpeechUtterance {
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(identifier: presentationVoiceIdentifier)
            ?? AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = rate
        return utterance
    }
}

@main
struct ACCAICoachApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 1120, minHeight: 720)
        }
        .windowStyle(.titleBar)
    }
}

final class ProcessBox: NSObject, ObservableObject, AVSpeechSynthesizerDelegate {
    @Published var running = false
    @Published var lines: [String] = []
    @Published var lastTelemetryPacketAt: Date?

    private var process: Process?
    private var pipe: Pipe?
    private var speakTimestampedLines = false
    private let speaker = RachelVoice.makeSynthesizer()
    private var outputBuffer = ""
    private var speechQueue: [String] = []

    override init() {
        super.init()
        speaker.delegate = self
    }

    func start(_ executable: String, args: [String], cwd: String, env: [String: String] = [:], speakTimestampedLines: Bool = false) throws {
        stop()
        self.speakTimestampedLines = speakTimestampedLines
        lastTelemetryPacketAt = nil
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = args
        process.currentDirectoryURL = URL(fileURLWithPath: cwd)
        var environment = ProcessInfo.processInfo.environment
        for (key, value) in env {
            environment[key] = value
        }
        process.environment = environment

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                self?.consumeOutput(text)
            }
        }

        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                self?.flushOutputBuffer()
                self?.running = false
            }
        }

        try process.run()
        self.process = process
        self.pipe = pipe
        running = true
        lines.append("Started: \(executable) \(args.joined(separator: " "))")
    }

    func stop() {
        speechQueue.removeAll()
        speaker.stopSpeaking(at: .immediate)
        lastTelemetryPacketAt = nil
        guard let process, process.isRunning else {
            running = false
            return
        }
        process.interrupt()
        DispatchQueue.global().asyncAfter(deadline: .now() + 2.0) {
            if process.isRunning {
                process.terminate()
            }
        }
        running = false
    }

    private func consumeOutput(_ text: String) {
        outputBuffer += text
        let parts = outputBuffer.components(separatedBy: "\n")
        outputBuffer = parts.last ?? ""
        let completeLines = parts.dropLast().filter { !$0.isEmpty }
        appendLines(Array(completeLines))
    }

    private func flushOutputBuffer() {
        guard !outputBuffer.isEmpty else { return }
        appendLines([outputBuffer])
        outputBuffer = ""
    }

    private func appendLines(_ newLines: [String]) {
        guard !newLines.isEmpty else { return }
        lines.append(contentsOf: newLines)
        if lines.count > 300 {
            lines.removeFirst(lines.count - 300)
        }
        if newLines.contains(where: isTelemetryPacketLine) {
            lastTelemetryPacketAt = Date()
        }
        if speakTimestampedLines {
            for line in newLines {
                speakIfCoachMessage(line)
            }
        }
    }

    func hasRecentTelemetryPacket(now: Date = Date()) -> Bool {
        guard running, let lastTelemetryPacketAt else { return false }
        return now.timeIntervalSince(lastTelemetryPacketAt) <= 4.0
    }

    private func isTelemetryPacketLine(_ line: String) -> Bool {
        let lower = line.lowercased()
        guard lower.contains("speed="),
              lower.contains("throttle="),
              lower.contains("brake=") else {
            return false
        }
        return lower.contains("sent=") || lower.contains("packets=")
    }

    private func speakIfCoachMessage(_ line: String) {
        guard line.hasPrefix("[") else { return }
        guard let close = line.firstIndex(of: "]") else { return }
        let messageStart = line.index(after: close)
        let message = line[messageStart...].trimmingCharacters(in: .whitespacesAndNewlines)
        guard !message.isEmpty else { return }
        let voiceText = compactVoiceText(message)
        if message.hasPrefix("For the upcoming corner") {
            speechQueue.removeAll()
            speechQueue.append(voiceText)
            if speaker.isSpeaking {
                speaker.stopSpeaking(at: .immediate)
                DispatchQueue.main.async { [weak self] in
                    self?.speakNextIfIdle()
                }
            } else {
                speakNextIfIdle()
            }
        } else {
            enqueueSpeech(voiceText)
        }
    }

    private func enqueueSpeech(_ text: String) {
        speechQueue.append(text)
        speakNextIfIdle()
    }

    private func speakNextIfIdle() {
        guard !speaker.isSpeaking, !speechQueue.isEmpty else { return }
        let next = speechQueue.removeFirst()
        speaker.speak(RachelVoice.utterance(next, rate: RachelVoice.liveRate))
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        DispatchQueue.main.async { [weak self] in
            self?.speakNextIfIdle()
        }
    }

    private func compactVoiceText(_ message: String) -> String {
        var text = message
        let replacements = [
            "Approaching ": "",
            "T1 La Source": "turn 1",
            "Turn 1 La Source": "turn 1",
            "T2, T3, T4 Eau Rouge/Raidillon, Kemmel": "turn 2, turn 3, turn 4",
            "T5, T6, T7 Les Combes/Malmedy": "turn 5, turn 6, turn 7",
            "T8, T9 Bruxelles": "turn 8, turn 9",
            "T10, T11 No Name/Pouhon entry": "turn 10, turn 11",
            "T12, T13 Pouhon/Fagnes": "turn 12, turn 13",
            "T14, T15 Campus/Stavelot": "turn 14, turn 15",
            "T16, T17 Blanchimont": "turn 16, turn 17",
            "T18, T19 Bus Stop chicane": "turn 18, turn 19 Bus Stop",
        ]
        for (source, target) in replacements {
            text = text.replacingOccurrences(of: source, with: target)
        }
        return text
    }
}

struct RunInfo: Identifiable, Hashable {
    var id: String { name }
    let name: String
    let modifiedAt: Date
    let packets: Int
    let track: String
    let level: String
    let score: Int?
    let hasReplay: Bool
    let hasDecision: Bool
    let hasProfile: Bool
    let hasSessionSummary: Bool
}

struct SessionAnalysis {
    let runName: String
    let track: String
    let level: String
    let overallScore: Int
    let scores: [(String, Int)]
    let strongest: [(String, Int)]
    let weakest: [(String, Int)]
    let recommendations: [String]
    let completedLaps: Int
    let validLaps: Int
    let invalidLaps: Int
    let incompleteLaps: Int
    let bestLap: String
}

enum AppPage: String, CaseIterable, Identifiable, Hashable {
    case home
    case drive
    case analyze
    case profile
    case proVideo
    case ai
    case setup
    case settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .home: return "Home"
        case .drive: return "Drive"
        case .analyze: return "Analyze"
        case .profile: return "Profile"
        case .proVideo: return "Pro Video"
        case .ai: return "AI"
        case .setup: return "Setup"
        case .settings: return "Settings"
        }
    }

    var icon: String {
        switch self {
        case .home: return "house.fill"
        case .drive: return "steeringwheel"
        case .analyze: return "waveform.path.ecg.rectangle"
        case .profile: return "person.text.rectangle"
        case .proVideo: return "video.badge.waveform"
        case .ai: return "brain.head.profile"
        case .setup: return "slider.horizontal.3"
        case .settings: return "gearshape"
        }
    }
}

enum TutorialTarget: String, Hashable {
    case appShell
    case homeNav
    case driveNav
    case analyzeNav
    case profileNav
    case proVideoNav
    case aiNav
    case setupNav
    case homeWorkspace
    case homeHero
    case coachIdentity
    case levelSelect
    case quickStart
    case sessionState
    case driveTrack
    case driveProReference
    case coachOutput
    case analyzeActions
    case radarPanel
    case sessionHistory
    case curriculum
    case aiSettings
    case proVideoSetup
    case setupCoach
}

struct TutorialStep: Identifiable, Hashable {
    let id: Int
    let page: AppPage
    let target: TutorialTarget
    let eyebrow: String
    let title: String
    let body: String
    let voice: String
}

struct TutorialTargetPreferenceKey: PreferenceKey {
    static var defaultValue: [TutorialTarget: CGRect] = [:]

    static func reduce(value: inout [TutorialTarget: CGRect], nextValue: () -> [TutorialTarget: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

final class AppModel: ObservableObject {
    @Published var selectedPage: AppPage = .home
    @Published var projectRoot: String
    @Published var projectDataStatus: String
    @Published var selectedMode = "Beginner"
    @Published var runName = ""
    @Published var trackMode = "Auto Detect"
    @Published var manualTrack = "Spa"
    @Published var detectedTrack = "Spa"
    @Published var isAnalyzing = false
    @Published var selectedRun: RunInfo?
    @Published var runs: [RunInfo] = []
    @Published var runListStatus = "Loading runs..."
    @Published var reportText = "Select a run, then open a report."
    @Published var sessionAnalysis: SessionAnalysis?
    @Published var selectedCurriculumStep = 0
    @Published var statusMessage = "Ready"
    @Published var openAIEnabled = false
    @Published var openAIAPIKey = ""
    @Published var openAIModel = "gpt-5-mini"
    @Published var openAIStatus = "AI phrasing is optional. The deterministic coach still works without API credits."
    @Published var proVideoPaths: [String] = []
    @Published var proVideoTrack = "Spa"
    @Published var proVideoCar = ""
    @Published var proVideoNotes = ""
    @Published var proVideoLapStart = ""
    @Published var proVideoLapEnd = ""
    @Published var proVideoStatus = "Select one or more MP4 files to build a visual reference."
    @Published var proVideoDataStatus = "No pro reference data captured yet."
    @Published var proVideoReportText = "No pro video reference generated yet."
    @Published var proVideoContactSheets: [String] = []
    @Published var useProVideoReference = false
    @Published var tutorialActive = false
    @Published var tutorialIndex = 0
    @Published var tutorialOverlayReady = false

    let rachel = ProcessBox()
    let helper = ProcessBox()
    private let testSpeaker = RachelVoice.makeSynthesizer()
    private var tutorialAutoAdvanceWorkItem: DispatchWorkItem?
    private var tutorialSpeechStartWorkItem: DispatchWorkItem?
    private let tutorialCompletedKey = "commercialTutorialCompleted"
    private let openAIKeychainService = "AI Racing Coach - ACC OpenAI API Key"
    private let openAIKeychainAccount = "default"

    init() {
        let resolvedProject = AppRuntime.resolveProjectRoot()
        projectRoot = resolvedProject.path
        projectDataStatus = resolvedProject.status
        openAIEnabled = UserDefaults.standard.bool(forKey: "openAIEnabled")
        openAIModel = UserDefaults.standard.string(forKey: "openAIModel") ?? "gpt-5-mini"
        openAIAPIKey = loadOpenAIKey()
        if !openAIAPIKey.isEmpty {
            openAIStatus = "OpenAI API key loaded from macOS Keychain."
        }
        runName = nextRunName(track: detectedTrack, level: selectedMode)
        refreshRuns()
    }

    var tutorialSteps: [TutorialStep] {
        [
            TutorialStep(
                id: 0,
                page: .home,
                target: .homeWorkspace,
                eyebrow: "Welcome",
                title: "Your ACC coaching workspace",
                body: "This Home page is the control center for Rachel, your current track, driving level, session history, driver curriculum, and latest coaching focus. You can start driving, review sessions, or continue training from here.",
                voice: "Welcome to ACC AI Coach. This Home page is your coaching workspace. I am Rachel, your private racing coach. From here, you can start a drive, review old sessions, and track your training focus."
            ),
            TutorialStep(
                id: 1,
                page: .home,
                target: .levelSelect,
                eyebrow: "Coach Level",
                title: "Choose the coaching depth",
                body: "Beginner focuses on valid laps, stability, and one clear correction at a time. Intermediate and Pro are prepared for deeper reference deltas and advanced setup work later.",
                voice: "Choose your coaching level here. Beginner keeps the advice simple and focused. Intermediate and Pro add deeper analysis later."
            ),
            TutorialStep(
                id: 2,
                page: .drive,
                target: .driveTrack,
                eyebrow: "Track Detection",
                title: "Let telemetry detect the track",
                body: "The app prefers ACC telemetry for track detection. Spa remains the current fallback, so you only need manual selection if auto-detection fails.",
                voice: "Track detection is automatic when telemetry is available. Spa is the fallback if the game does not expose the track name."
            ),
            TutorialStep(
                id: 3,
                page: .drive,
                target: .quickStart,
                eyebrow: "Drive",
                title: "Start and stop a coached session",
                body: "Run starts Rachel and the CrossOver telemetry helper together. Stop ends the drive and automatically builds replay, validation, profile, reference comparison, and session scoring reports.",
                voice: "Press Run when ACC is in a driving session. Press Stop when you are done. I will build the reports automatically."
            ),
            TutorialStep(
                id: 4,
                page: .drive,
                target: .driveProReference,
                eyebrow: "Reference Mode",
                title: "Compare against a pro video reference",
                body: "Turn this on when you want Rachel to prioritize the largest difference versus your latest analyzed pro MP4 reference. Major invalid-lap incidents still override small pro-reference deltas.",
                voice: "Use Pro Video Reference compares your drive to the latest analyzed pro video. Big invalid-lap incidents still come first."
            ),
            TutorialStep(
                id: 5,
                page: .drive,
                target: .coachOutput,
                eyebrow: "Live Feedback",
                title: "Watch Rachel's live reasoning",
                body: "This feed mirrors the real-time coach messages, helper status, packets, and final session output. Voice remains low-distraction while the text feed keeps the evidence visible.",
                voice: "The Rachel Output panel shows the same coaching evidence that I use for voice guidance."
            ),
            TutorialStep(
                id: 6,
                page: .analyze,
                target: .analyzeActions,
                eyebrow: "Post Session",
                title: "Generate and inspect reports",
                body: "Replay rebuilds Rachel's messages from saved telemetry. Validate checks milestone acceptance. Profile updates driver memory. Session Summary creates the scored review and radar.",
                voice: "The Analyze buttons rebuild reports from saved sessions, so you can debug and review without driving new laps."
            ),
            TutorialStep(
                id: 7,
                page: .analyze,
                target: .radarPanel,
                eyebrow: "Scoring",
                title: "Use the radar to find weak areas",
                body: "The radar summarizes telemetry quality, consistency, braking, rotation, throttle, and stability. The explanation panel says why each category scored high or low.",
                voice: "The radar shows your session score by category. Use the explanation beside it to understand why a score is low."
            ),
            TutorialStep(
                id: 8,
                page: .profile,
                target: .curriculum,
                eyebrow: "Driver Memory",
                title: "Turn repeated mistakes into a curriculum",
                body: "Rachel tracks whether an issue is a one-time mistake, a repeated session problem, or a real habit. The curriculum chooses what to fix first and what to ignore for now.",
                voice: "The curriculum turns repeated driving patterns into a focused training plan."
            ),
            TutorialStep(
                id: 9,
                page: .proVideo,
                target: .proVideoSetup,
                eyebrow: "Pro Video",
                title: "Build a pro MP4 reference",
                body: "Choose an MP4, enter the reference lap start and end, then analyze it. Steering, throttle, and brake come from the lower-right HUD. Speed and gear are attempted from the cockpit wheel display only when confidence is high.",
                voice: "Use this page to analyze pro driver videos. Steering, throttle, and brake come from the lower-right HUD. Speed and gear are only trusted if the cockpit display is readable."
            ),
            TutorialStep(
                id: 10,
                page: .ai,
                target: .aiSettings,
                eyebrow: "Optional AI",
                title: "Connect OpenAI only when you need it",
                body: "The local deterministic coach works without API credits. If you add an OpenAI API key later, Rachel can improve post-session phrasing and planning without replacing telemetry evidence.",
                voice: "The coach works locally without API credits. OpenAI is optional for better post-session wording and planning."
            ),
            TutorialStep(
                id: 11,
                page: .setup,
                target: .setupCoach,
                eyebrow: "Future Module",
                title: "Setup analysis is intentionally parked",
                body: "Setup Coach has a polished placeholder, but it stays disabled until the app can separate driving mistakes from setup problems. This keeps the current advice honest.",
                voice: "Setup Coach is parked for now. We will not give setup advice until the data can separate driving problems from setup problems."
            ),
            TutorialStep(
                id: 12,
                page: .home,
                target: .sessionHistory,
                eyebrow: "Ready",
                title: "Review old runs anytime",
                body: "Your run history stays local on this Mac. Open old sessions, regenerate reports, compare progress, or replay Rachel's coaching without launching ACC.",
                voice: "Your run history stays local. You can review old sessions and replay coaching without launching ACC."
            ),
        ]
    }

    var currentTutorialStep: TutorialStep? {
        guard tutorialActive, tutorialSteps.indices.contains(tutorialIndex) else { return nil }
        return tutorialSteps[tutorialIndex]
    }

    var tutorialProgressText: String {
        guard !tutorialSteps.isEmpty else { return "0 of 0" }
        return "\(min(tutorialIndex + 1, tutorialSteps.count)) of \(tutorialSteps.count)"
    }

    var accTelemetryLive: Bool {
        helper.hasRecentTelemetryPacket() || rachel.hasRecentTelemetryPacket()
    }

    func triggerFirstRunTutorialIfNeeded() {
        guard !UserDefaults.standard.bool(forKey: tutorialCompletedKey) else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
            self.startTutorial(resetCompletion: false)
        }
    }

    func startTutorial(resetCompletion: Bool = true) {
        if resetCompletion {
            UserDefaults.standard.set(false, forKey: tutorialCompletedKey)
        }
        tutorialIndex = 0
        tutorialActive = true
        applyTutorialStep()
    }

    func nextTutorialStep() {
        guard tutorialActive else { return }
        cancelTutorialAutoAdvance()
        testSpeaker.stopSpeaking(at: .immediate)
        if tutorialIndex + 1 >= tutorialSteps.count {
            finishTutorial()
        } else {
            tutorialIndex += 1
            applyTutorialStep()
        }
    }

    func previousTutorialStep() {
        guard tutorialActive, tutorialIndex > 0 else { return }
        cancelTutorialAutoAdvance()
        testSpeaker.stopSpeaking(at: .immediate)
        tutorialIndex -= 1
        applyTutorialStep()
    }

    func skipTutorial() {
        cancelTutorialAutoAdvance()
        cancelTutorialSpeechStart()
        testSpeaker.stopSpeaking(at: .immediate)
        tutorialActive = false
        UserDefaults.standard.set(true, forKey: tutorialCompletedKey)
        statusMessage = "Tutorial skipped."
    }

    func finishTutorial() {
        cancelTutorialAutoAdvance()
        cancelTutorialSpeechStart()
        testSpeaker.stopSpeaking(at: .immediate)
        tutorialActive = false
        UserDefaults.standard.set(true, forKey: tutorialCompletedKey)
        statusMessage = "Tutorial complete."
        speakTutorialCompletion()
    }

    private func applyTutorialStep() {
        cancelTutorialAutoAdvance()
        cancelTutorialSpeechStart()
        testSpeaker.stopSpeaking(at: .immediate)
        guard let step = currentTutorialStep else { return }
        tutorialOverlayReady = false
        selectedPage = step.page
        let speechDelay = 0.16
        let speechItem = DispatchWorkItem { [weak self] in
            guard let self,
                  self.tutorialActive,
                  self.currentTutorialStep?.id == step.id else { return }
            self.speakTutorial(step.voice, stepID: step.id)
        }
        tutorialSpeechStartWorkItem = speechItem
        DispatchQueue.main.asyncAfter(deadline: .now() + speechDelay, execute: speechItem)
    }

    private func speakTutorial(_ text: String, stepID: Int) {
        testSpeaker.stopSpeaking(at: .immediate)
        testSpeaker.speak(RachelVoice.utterance(text, rate: RachelVoice.tutorialRate))
        scheduleTutorialAutoAdvance(for: stepID, extraDelay: 0.18)
    }

    private func speakTutorialCompletion() {
        testSpeaker.speak(RachelVoice.utterance(
            "Now the tutorial is all complete. You are ready to start a coached session with Rachel.",
            rate: RachelVoice.tutorialRate
        ))
    }

    private func scheduleTutorialAutoAdvance(for stepID: Int, extraDelay: Double = 0) {
        let item = DispatchWorkItem { [weak self] in
            self?.advanceTutorialWhenSpeechIsIdle(stepID: stepID)
        }
        tutorialAutoAdvanceWorkItem = item
        DispatchQueue.main.asyncAfter(deadline: .now() + extraDelay, execute: item)
    }

    private func advanceTutorialWhenSpeechIsIdle(stepID: Int) {
        guard tutorialActive,
              currentTutorialStep?.id == stepID else { return }
        guard !testSpeaker.isSpeaking else {
            scheduleTutorialAutoAdvance(for: stepID, extraDelay: 0.12)
            return
        }
        let item = DispatchWorkItem { [weak self] in
            guard let self,
                  self.tutorialActive,
                  self.currentTutorialStep?.id == stepID,
                  !self.testSpeaker.isSpeaking else { return }
            self.nextTutorialStep()
        }
        tutorialAutoAdvanceWorkItem = item
        DispatchQueue.main.asyncAfter(deadline: .now() + tutorialAutoAdvanceDelay(for: currentTutorialStep!), execute: item)
    }

    private func tutorialAutoAdvanceDelay(for step: TutorialStep) -> Double {
        let wordCount = step.voice.split(separator: " ").count
        let estimatedSpeechSeconds = Double(wordCount) / 3.15
        _ = estimatedSpeechSeconds + 0.05
        return min(0.22, max(0.12, Double(wordCount) * 0.002))
    }

    private func cancelTutorialAutoAdvance() {
        tutorialAutoAdvanceWorkItem?.cancel()
        tutorialAutoAdvanceWorkItem = nil
    }

    private func cancelTutorialSpeechStart() {
        tutorialSpeechStartWorkItem?.cancel()
        tutorialSpeechStartWorkItem = nil
    }

    func revealTutorialOverlay(for stepID: Int, after delay: Double) {
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self,
                  self.tutorialActive,
                  self.currentTutorialStep?.id == stepID else { return }
            withAnimation(.easeOut(duration: 0.06)) {
                self.tutorialOverlayReady = true
            }
        }
    }

    func refreshRuns() {
        let runsURL = URL(fileURLWithPath: projectRoot).appendingPathComponent("runs")
        let files: [URL]
        do {
            files = try FileManager.default.contentsOfDirectory(at: runsURL, includingPropertiesForKeys: [.contentModificationDateKey], options: [.skipsHiddenFiles])
        } catch {
            runListStatus = "Could not read runs folder: \(error.localizedDescription). Connect the project folder to restore previous runs."
            projectDataStatus = "Run history unavailable. Connect your ACC AI Coach project folder."
            statusMessage = "Run history needs folder access."
            return
        }
        let mapped = files.compactMap { url -> RunInfo? in
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue else { return nil }
            let summaryURL = url.appendingPathComponent("summary.json")
            let sessionURL = url.appendingPathComponent("session_summary.json")
            let telemetryURL = url.appendingPathComponent("telemetry.csv")
            guard FileManager.default.fileExists(atPath: summaryURL.path) || FileManager.default.fileExists(atPath: telemetryURL.path) else { return nil }
            let values = try? url.resourceValues(forKeys: [.contentModificationDateKey])
            let summary = Self.readJSON(summaryURL)
            let session = Self.readJSON(sessionURL)
            let app = summary["app"] as? [String: Any]
            let tracks = summary["tracks_seen"] as? [String]
            let packets = Self.packetCount(summary)
            return RunInfo(
                name: url.lastPathComponent,
                modifiedAt: values?.contentModificationDate ?? Date.distantPast,
                packets: packets,
                track: (app?["detected_track"] as? String) ?? tracks?.first ?? (session["track"] as? String) ?? "Unknown",
                level: (app?["coach_level"] as? String) ?? (session["driving_level"] as? String) ?? "Unknown",
                score: session["overall_score"] as? Int,
                hasReplay: FileManager.default.fileExists(atPath: url.appendingPathComponent("coach_replay.md").path),
                hasDecision: FileManager.default.fileExists(atPath: url.appendingPathComponent("coach_decision_report.md").path),
                hasProfile: FileManager.default.fileExists(atPath: url.appendingPathComponent("driver_profile_update.md").path),
                hasSessionSummary: FileManager.default.fileExists(atPath: url.appendingPathComponent("session_summary.md").path)
            )
        }
        runs = mapped.sorted { $0.modifiedAt > $1.modifiedAt }
        runListStatus = runs.isEmpty ? "No runs found in \(runsURL.path)" : "\(runs.count) runs loaded"
        if let selectedRun, let refreshed = runs.first(where: { $0.name == selectedRun.name }) {
            self.selectedRun = refreshed
        } else {
            selectedRun = runs.first
        }
    }

    func connectProjectFolder() {
        let panel = NSOpenPanel()
        panel.title = "Connect ACC AI Coach Project Folder"
        panel.message = "Select the ACC AI COACH project folder that contains tools and runs."
        panel.prompt = "Connect"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = URL(fileURLWithPath: AppRuntime.legacyProjectRoot)

        guard panel.runModal() == .OK, let url = panel.url else {
            statusMessage = "Project folder connection cancelled."
            return
        }

        guard AppRuntime.isUsableProjectRoot(url.path) else {
            projectDataStatus = "Selected folder rejected. It must contain tools/realtime_coach.py."
            statusMessage = "Invalid project folder."
            return
        }

        projectRoot = url.path
        UserDefaults.standard.set(url.path, forKey: AppRuntime.projectRootOverrideKey)
        projectDataStatus = "Using connected project folder."
        statusMessage = "Project folder connected."
        refreshRuns()
        openLatestProVideoReport()
        runName = nextRunName(track: resolvedTrack(), level: selectedMode)
    }

    func refreshExternalAppStatus() {
        objectWillChange.send()
    }

    static func readJSON(_ url: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return json
    }

    static func packetCount(_ json: [String: Any]) -> Int {
        return (json["packet_count"] as? Int) ?? (json["packets"] as? Int) ?? 0
    }

    func startDriving() {
        let track = resolvedTrack()
        let sanitized = sanitizeRunName(runName.isEmpty ? nextRunName(track: track, level: selectedMode) : runName)
        runName = sanitized
        let python = pythonExecutable()
        let realtime = "\(projectRoot)/tools/realtime_coach.py"
        var realtimeArgs = ["-u", realtime, "--run-name", sanitized, "--coach-level", selectedMode, "--track", trackMode == "Auto Detect" ? "auto" : track]
        if openAIEnabled && !openAIAPIKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            realtimeArgs.append(contentsOf: ["--ai-coach", "--ai-model", openAIModel])
        }
        if useProVideoReference {
            realtimeArgs.append(contentsOf: ["--pro-video-reference", "latest"])
        }
        var realtimeEnv = [
            "PYTHONPATH": "\(projectRoot)/tools",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        ]
        if openAIEnabled && !openAIAPIKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            realtimeEnv["OPENAI_API_KEY"] = openAIAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        do {
            try rachel.start(
                python,
                args: realtimeArgs,
                cwd: projectRoot,
                env: realtimeEnv,
                speakTimestampedLines: true
            )
            try helper.start(
                "/bin/bash",
                args: ["\(projectRoot)/windows-helper/AccTelemetryForwarderNetFx/run-in-crossover.sh"],
                cwd: projectRoot
            )
            statusMessage = "Rachel and telemetry helper are running."
            writeRunMetadata(runName: sanitized, track: track, level: selectedMode)
        } catch {
            rachel.stop()
            helper.stop()
            statusMessage = "Start failed: \(error.localizedDescription)"
        }
    }

    func stopDriving() {
        rachel.stop()
        helper.stop()
        statusMessage = "Stopped. Refreshing runs."
        isAnalyzing = true
        let completedRun = runName
        let completedLevel = selectedMode
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) {
            self.runPostSessionPipeline(runName: completedRun, level: completedLevel)
        }
    }

    func testVoice() {
        testSpeaker.stopSpeaking(at: .immediate)
        testSpeaker.speak(RachelVoice.utterance(
            "Hi, I am Rachel. Voice output is working from the native app.",
            rate: RachelVoice.liveRate
        ))
        statusMessage = "Voice test started."
    }

    func generateReplay() {
        guard let selectedRun else { return }
        runTool("replay_coach.py", args: [selectedRun.name])
        openReport("coach_replay.md")
    }

    func generateSessionSummary() {
        guard let selectedRun else { return }
        runTool("session_summary.py", args: [selectedRun.name, "--level", selectedRun.level == "Unknown" ? selectedMode : selectedRun.level])
        loadSessionAnalysis(for: selectedRun.name)
        openReport("session_summary.md")
    }

    func openRadar() {
        guard let selectedRun else { return }
        loadSessionAnalysis(for: selectedRun.name)
        reportText = sessionAnalysis == nil ? "Radar data not found. Generate Session Summary first." : "Radar loaded inside Analyze."
    }

    func validate() {
        guard let selectedRun else { return }
        runTool("validate_milestone_acceptance.py", args: [selectedRun.name])
        openMilestoneAcceptanceReport()
    }

    func updateProfile() {
        guard let selectedRun else { return }
        runTool("driver_model.py", args: ["runs/\(selectedRun.name)"])
        openReport("driver_profile_update.md")
    }

    func generateAICoach() {
        guard let selectedRun else {
            reportText = "Select a run first."
            return
        }
        runTool("ai_coach.py", args: [selectedRun.name, "--model", openAIModel, "--debug-error"])
        openReport("ai_coach_output.md")
    }

    func chooseProVideos() {
        let panel = NSOpenPanel()
        panel.title = "Choose pro driver videos"
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowedContentTypes = [.mpeg4Movie, .movie, .video]
        if panel.runModal() == .OK {
            proVideoPaths = panel.urls.map { $0.path }
            proVideoStatus = "\(proVideoPaths.count) video file(s) selected."
        }
    }

    func analyzeProVideos() {
        guard !proVideoPaths.isEmpty else {
            proVideoStatus = "Choose at least one video first."
            return
        }
        let paths = proVideoPaths
        let track = proVideoTrack
        let car = proVideoCar
        let notes = proVideoNotes
        let lapStart = proVideoLapStart.trimmingCharacters(in: .whitespacesAndNewlines)
        let lapEnd = proVideoLapEnd.trimmingCharacters(in: .whitespacesAndNewlines)
        let python = pythonExecutable()
        let root = projectRoot
        DispatchQueue.main.async {
            self.proVideoStatus = "Analyzing video reference..."
            self.proVideoDataStatus = "Data capture running..."
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: python)
            var arguments = ["-u", "\(root)/tools/pro_video_reference.py"] + paths + ["--track", track, "--car", car, "--notes", notes]
            if !lapStart.isEmpty {
                arguments.append(contentsOf: ["--lap-start", lapStart])
            }
            if !lapEnd.isEmpty {
                arguments.append(contentsOf: ["--lap-end", lapEnd])
            }
            process.arguments = arguments
            process.currentDirectoryURL = URL(fileURLWithPath: root)
            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONPATH"] = "\(root)/tools"
            environment["PYTHONUNBUFFERED"] = "1"
            process.environment = environment
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            do {
                try process.run()
                process.waitUntilExit()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                let output = String(data: data, encoding: .utf8) ?? ""
                DispatchQueue.main.async {
                    self.proVideoReportText = output.isEmpty ? "Pro video analysis finished." : output
                    if process.terminationStatus == 0 {
                        self.proVideoStatus = "Pro video reference ready."
                        self.openLatestProVideoReport()
                    } else {
                        self.proVideoStatus = "Pro video analysis failed. Check the message below."
                        self.proVideoDataStatus = "Data capture failed. Latest reference was not changed."
                        self.proVideoContactSheets = []
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    self.proVideoStatus = "Pro video analysis failed: \(error.localizedDescription)"
                    self.proVideoDataStatus = "Data capture failed before the analyzer started."
                }
            }
        }
    }

    func openLatestProVideoReport() {
        let latestURL = URL(fileURLWithPath: projectRoot)
            .appendingPathComponent("data")
            .appendingPathComponent("pro_video_references")
            .appendingPathComponent("latest_pro_video_reference.json")
        let latest = Self.readJSON(latestURL)
        guard let reportPath = latest["report"] as? String else {
            proVideoReportText = "No pro video reference report found yet."
            proVideoContactSheets = []
            proVideoDataStatus = "No pro reference data captured yet."
            return
        }
        guard let text = try? String(contentsOfFile: reportPath, encoding: .utf8) else {
            proVideoReportText = "Could not open pro video report: \(reportPath)"
            proVideoContactSheets = []
            proVideoDataStatus = "Report exists in latest pointer, but the file could not be opened."
            return
        }
        proVideoReportText = text
        proVideoContactSheets = Self.readProVideoContactSheets(latest)
        proVideoDataStatus = Self.proVideoReferenceStatus(latest)
        proVideoStatus = "Loaded latest pro video reference."
    }

    static func readProVideoContactSheets(_ latest: [String: Any]) -> [String] {
        guard let manifestPath = latest["manifest"] as? String else { return [] }
        let manifest = readJSON(URL(fileURLWithPath: manifestPath))
        guard let entries = manifest["entries"] as? [[String: Any]] else { return [] }
        return entries.compactMap { entry in
            guard let visual = entry["visual_reference"] as? [String: Any],
                  let sheet = visual["contact_sheet"] as? String,
                  FileManager.default.fileExists(atPath: sheet) else {
                return nil
            }
            return sheet
        }
    }

    static func proVideoReferenceStatus(_ latest: [String: Any]) -> String {
        guard let referencePath = latest["pro_reference"] as? String else {
            return "Visual report loaded, but no Rachel-compatible pro data reference was generated."
        }
        let reference = readJSON(URL(fileURLWithPath: referencePath))
        let lapProfile = reference["lap_profile"] as? [String: Any]
        let zones = lapProfile?["zones"] as? [String: Any]
        let samples = lapProfile?["input_trace_sample"] as? [[String: Any]]
        let metrics = lapProfile?["available_metrics"] as? [String: Any]
        let gearDetection = lapProfile?["gear_detection"] as? [String: Any]
        let speedDetection = lapProfile?["speed_detection"] as? [String: Any]
        let zoneCount = zones?.count ?? 0
        let sampleCount = samples?.count ?? 0
        if zoneCount > 0 && sampleCount > 0 {
            let hasSpeed = (metrics?["turn_entry_exit_speed"] as? Bool) == true
            let hasGear = (metrics?["turn_entry_exit_gear"] as? Bool) == true
            let gearText = hasGear ? "gear ready" : ((gearDetection?["attempted"] as? Bool) == true ? "gear attempted, low confidence" : "gear unavailable")
            let speedText = hasSpeed ? "speed ready" : ((speedDetection?["attempted"] as? Bool) == true ? "speed attempted, low confidence" : "speed unavailable")
            return "Data capture succeeded: \(sampleCount) HUD samples, \(zoneCount) Spa zone groups, steering/throttle/brake/time nodes ready, \(gearText), \(speedText)."
        }
        return "Data capture incomplete: visual report loaded, but no usable HUD input trace was found."
    }

    func testOpenAIConnection() {
        guard openAIEnabled else {
            openAIStatus = "Turn on OpenAI first, then test the connection."
            return
        }
        guard !openAIAPIKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            openAIStatus = "Paste and save an OpenAI API key before testing."
            return
        }
        runTool("test_openai_connection.py", args: ["--model", openAIModel])
        openAIStatus = reportText.contains("OpenAI connection: OK") ? "OpenAI connection test passed." : "OpenAI connection test failed. Check the Analyze output for the exact reason."
    }

    func runTool(_ script: String, args: [String]) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonExecutable())
        process.arguments = ["-u", "\(projectRoot)/tools/\(script)"] + args
        process.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = "\(projectRoot)/tools"
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if openAIEnabled && !openAIAPIKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            environment["OPENAI_API_KEY"] = openAIAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let output = String(data: data, encoding: .utf8) ?? ""
            statusMessage = process.terminationStatus == 0 ? "Command finished." : "Command failed."
            reportText = output.isEmpty ? statusMessage : output
            refreshRuns()
        } catch {
            statusMessage = "Command failed: \(error.localizedDescription)"
        }
    }

    private func runPostSessionPipeline(runName: String, level: String) {
        DispatchQueue.global(qos: .userInitiated).async {
            let steps = [
                ("replay_coach.py", [runName]),
                ("reference_compare.py", ["compare", runName]),
                ("driver_model.py", ["runs/\(runName)"]),
                ("session_summary.py", [runName, "--level", level]),
            ]
            var output = ""
            for (script, args) in steps {
                output += self.runToolOutput(script, args: args)
                output += "\n"
            }
            DispatchQueue.main.async {
                self.reportText = output.trimmingCharacters(in: .whitespacesAndNewlines)
                self.statusMessage = "Post-session analysis complete."
                self.isAnalyzing = false
                self.refreshRuns()
                if let selected = self.runs.first(where: { $0.name == runName }) {
                    self.selectedRun = selected
                    let summary = Self.readJSON(URL(fileURLWithPath: self.projectRoot).appendingPathComponent("runs").appendingPathComponent(runName).appendingPathComponent("summary.json"))
                    let app = summary["app"] as? [String: Any]
                    let detected = app?["detected_track"] as? String
                    if let detected, !detected.isEmpty {
                        self.detectedTrack = detected
                    }
                }
                self.runName = self.nextRunName(track: self.detectedTrack, level: self.selectedMode)
            }
        }
    }

    private func runToolOutput(_ script: String, args: [String]) -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonExecutable())
        process.arguments = ["-u", "\(projectRoot)/tools/\(script)"] + args
        process.currentDirectoryURL = URL(fileURLWithPath: projectRoot)
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = "\(projectRoot)/tools"
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8) ?? ""
        } catch {
            return "Command failed: \(script) \(error.localizedDescription)"
        }
    }

    func openReport(_ name: String) {
        guard let selectedRun else { return }
        let url = URL(fileURLWithPath: projectRoot)
            .appendingPathComponent("runs")
            .appendingPathComponent(selectedRun.name)
            .appendingPathComponent(name)
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            reportText = "Report not found: \(name)"
            return
        }
        reportText = text
        if name == "session_summary.md" {
            loadSessionAnalysis(for: selectedRun.name)
        }
    }

    func openMilestoneAcceptanceReport() {
        let url = URL(fileURLWithPath: projectRoot)
            .appendingPathComponent("runs")
            .appendingPathComponent("milestone_acceptance_report.md")
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            reportText = "Report not found: milestone_acceptance_report.md"
            return
        }
        reportText = text
    }

    func loadSessionAnalysis(for runName: String) {
        let url = URL(fileURLWithPath: projectRoot)
            .appendingPathComponent("runs")
            .appendingPathComponent(runName)
            .appendingPathComponent("session_summary.json")
        let json = Self.readJSON(url)
        guard !json.isEmpty else {
            sessionAnalysis = nil
            return
        }
        let score = json["overall_score"] as? Int ?? 0
        let laps = json["laps"] as? [String: Any] ?? [:]
        let scoresDict = json["scores"] as? [String: Any] ?? [:]
        let scoreOrder = ["Telemetry", "Consistency", "Braking", "Rotation", "Throttle", "Stability"]
        let scores = scoreOrder.map { ($0, scoresDict[$0] as? Int ?? 0) }
        let strongest = Self.scorePairs(json["strongest_areas"])
        let weakest = Self.scorePairs(json["weakest_areas"])
        let recommendations = (json["recommendations"] as? [[String: Any]] ?? []).prefix(5).map { item in
            let zone = item["zone"] as? String ?? "Unknown"
            let label = item["label"] as? String ?? "Issue"
            let recommendation = item["recommendation"] as? String ?? ""
            return "\(zone) - \(label): \(recommendation)"
        }
        sessionAnalysis = SessionAnalysis(
            runName: json["run_name"] as? String ?? runName,
            track: json["track"] as? String ?? "Unknown",
            level: json["driving_level"] as? String ?? "Unknown",
            overallScore: score,
            scores: scores,
            strongest: strongest,
            weakest: weakest,
            recommendations: recommendations,
            completedLaps: laps["completed"] as? Int ?? 0,
            validLaps: laps["valid"] as? Int ?? 0,
            invalidLaps: laps["invalid"] as? Int ?? 0,
            incompleteLaps: laps["incomplete"] as? Int ?? 0,
            bestLap: laps["best_lap_display"] as? String ?? "n/a"
        )
    }

    static func scorePairs(_ value: Any?) -> [(String, Int)] {
        guard let rows = value as? [[Any]] else { return [] }
        return rows.compactMap { row in
            guard row.count >= 2, let name = row[0] as? String else { return nil }
            if let score = row[1] as? Int {
                return (name, score)
            }
            if let score = row[1] as? Double {
                return (name, Int(score))
            }
            return nil
        }
    }

    private func sanitizeRunName(_ value: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "._-"))
        let mapped = value.unicodeScalars.map { allowed.contains($0) ? Character($0) : "-" }
        let result = String(mapped).trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        return result.isEmpty ? "acc-m5-native-\(Int(Date().timeIntervalSince1970))" : result
    }

    private func resolvedTrack() -> String {
        if trackMode == "Manual" {
            return manualTrack
        }
        return detectedTrack.isEmpty ? "Spa" : detectedTrack
    }

    func nextRunName(track: String, level: String) -> String {
        let displayTrack = track.isEmpty || track == "Unknown" ? "Spa" : track
        let prefix = "\(sanitizeRunName(displayTrack))-\(level.lowercased())-run"
        let next = runs
            .map { $0.name }
            .compactMap { name -> Int? in
                guard name.hasPrefix(prefix) else { return nil }
                return Int(name.replacingOccurrences(of: "\(prefix)-", with: ""))
            }
            .max()
            .map { $0 + 1 } ?? 1
        return "\(prefix)-\(String(format: "%02d", next))"
    }

    func refreshRunName() {
        runName = nextRunName(track: resolvedTrack(), level: selectedMode)
    }

    private func writeRunMetadata(runName: String, track: String, level: String) {
        let runURL = URL(fileURLWithPath: projectRoot).appendingPathComponent("runs").appendingPathComponent(runName)
        try? FileManager.default.createDirectory(at: runURL, withIntermediateDirectories: true)
        let url = runURL.appendingPathComponent("run_metadata.json")
        let metadata: [String: Any] = [
            "run_name": runName,
            "track_hint": track,
            "track_mode": trackMode,
            "driving_level": level,
            "pro_video_reference_enabled": useProVideoReference,
            "started_at": ISO8601DateFormatter().string(from: Date()),
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: metadata, options: [.prettyPrinted]) else { return }
        try? data.write(to: url)
    }

    private func pythonExecutable() -> String {
        if FileManager.default.isExecutableFile(atPath: preferredPython) {
            return preferredPython
        }
        return "/usr/bin/python3"
    }

    func saveOpenAISettings() {
        UserDefaults.standard.set(openAIEnabled, forKey: "openAIEnabled")
        UserDefaults.standard.set(openAIModel, forKey: "openAIModel")
        let trimmed = openAIAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            openAIStatus = "Paste an OpenAI API key before saving."
            return
        }
        let result = runSecurityCommand([
            "add-generic-password",
            "-U",
            "-s", openAIKeychainService,
            "-a", openAIKeychainAccount,
            "-w", trimmed,
        ])
        openAIStatus = result.ok ? "OpenAI API key saved to macOS Keychain." : "Keychain save failed: \(result.output)"
    }

    func clearOpenAIKey() {
        _ = runSecurityCommand([
            "delete-generic-password",
            "-s", openAIKeychainService,
            "-a", openAIKeychainAccount,
        ])
        openAIAPIKey = ""
        openAIEnabled = false
        UserDefaults.standard.set(false, forKey: "openAIEnabled")
        openAIStatus = "OpenAI API key removed from macOS Keychain."
    }

    private func loadOpenAIKey() -> String {
        let result = runSecurityCommand([
            "find-generic-password",
            "-s", openAIKeychainService,
            "-a", openAIKeychainAccount,
            "-w",
        ])
        return result.ok ? result.output.trimmingCharacters(in: .whitespacesAndNewlines) : ""
    }

    private func runSecurityCommand(_ args: [String]) -> (ok: Bool, output: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = args
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let output = String(data: data, encoding: .utf8) ?? ""
            return (process.terminationStatus == 0, output.trimmingCharacters(in: .whitespacesAndNewlines))
        } catch {
            return (false, error.localizedDescription)
        }
    }
}

struct ContentView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationSplitView {
            Sidebar()
                .navigationSplitViewColumnWidth(min: 260, ideal: 300, max: 360)
        } detail: {
            ZStack {
                AppTheme.background.ignoresSafeArea()
                Group {
                    switch model.selectedPage {
                    case .home:
                        HomeView()
                    case .drive:
                        DriveView()
                    case .analyze:
                        AnalyzeView()
                    case .profile:
                        ProfileView()
                    case .proVideo:
                        ProVideoView()
                    case .ai:
                        AISettingsView()
                    case .setup:
                        SetupView()
                    case .settings:
                        SettingsView()
                    }
                }
                .id(model.selectedPage)
                .foregroundStyle(.primary)
                .transition(model.tutorialActive ? .identity : .asymmetric(
                    insertion: .opacity.combined(with: .offset(x: 8, y: 0)),
                    removal: .opacity.combined(with: .offset(x: -6, y: 0))
                ))
                .animation(.easeInOut(duration: model.tutorialActive ? 0.10 : 0.16), value: model.selectedPage)
                .compositingGroup()
            }
        }
        .tutorialTarget(.appShell)
        .overlayPreferenceValue(TutorialTargetPreferenceKey.self) { anchors in
            TutorialOverlay(anchors: anchors)
                .environmentObject(model)
        }
        .toolbar(removing: .sidebarToggle)
        .coordinateSpace(name: "TutorialRoot")
        .onAppear {
            model.triggerFirstRunTutorialIfNeeded()
        }
    }
}

enum AppTheme {
    static let background = dynamic(
        light: NSColor(calibratedRed: 0.88, green: 0.90, blue: 0.91, alpha: 1),
        dark: NSColor(calibratedRed: 0.24, green: 0.25, blue: 0.26, alpha: 1)
    )
    static let panel = dynamic(
        light: NSColor(calibratedRed: 0.96, green: 0.97, blue: 0.98, alpha: 0.82),
        dark: NSColor(calibratedRed: 0.26, green: 0.27, blue: 0.29, alpha: 0.76)
    )
    static let panelSoft = dynamic(
        light: NSColor(calibratedRed: 1.0, green: 1.0, blue: 1.0, alpha: 0.64),
        dark: NSColor(calibratedRed: 0.34, green: 0.35, blue: 0.37, alpha: 0.68)
    )
    static let border = dynamic(
        light: NSColor(calibratedWhite: 0.0, alpha: 0.10),
        dark: NSColor(calibratedWhite: 1.0, alpha: 0.15)
    )
    static let gold = Color(nsColor: NSColor(calibratedRed: 0.83, green: 0.64, blue: 0.31, alpha: 1))
    static let cyan = Color(nsColor: NSColor(calibratedRed: 0.28, green: 0.72, blue: 0.78, alpha: 1))
    static let green = Color(nsColor: NSColor(calibratedRed: 0.28, green: 0.78, blue: 0.48, alpha: 1))
    static let yellow = Color(nsColor: NSColor(calibratedRed: 0.95, green: 0.72, blue: 0.22, alpha: 1))
    static let red = Color(nsColor: NSColor(calibratedRed: 0.92, green: 0.25, blue: 0.29, alpha: 1))

    private static func dynamic(light: NSColor, dark: NSColor) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let match = appearance.bestMatch(from: [.darkAqua, .aqua])
            return match == .darkAqua ? dark : light
        })
    }
}

extension View {
    func appPanel() -> some View {
        self
            .padding(16)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
            .background(AppTheme.panel, in: RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(AppTheme.border, lineWidth: 1)
            )
    }

    func tutorialTarget(_ target: TutorialTarget) -> some View {
        id(target.rawValue)
            .background(
            GeometryReader { proxy in
                Color.clear.preference(
                    key: TutorialTargetPreferenceKey.self,
                    value: [target: proxy.frame(in: .named("TutorialRoot"))]
                )
            }
        )
    }
}

struct TutorialPageScrollView<Content: View>: View {
    @EnvironmentObject private var model: AppModel
    let page: AppPage
    @ViewBuilder let content: Content

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                content
            }
            .onAppear {
                scrollToTutorialTarget(proxy)
            }
            .onChange(of: model.tutorialIndex) { _, _ in
                scrollToTutorialTarget(proxy)
            }
            .onChange(of: model.tutorialActive) { _, _ in
                scrollToTutorialTarget(proxy)
            }
        }
    }

    private func scrollToTutorialTarget(_ proxy: ScrollViewProxy) {
        guard model.tutorialActive,
              let step = model.currentTutorialStep,
              step.page == page else { return }
        guard step.target != .homeWorkspace else {
            model.revealTutorialOverlay(for: step.id, after: 0.025)
            return
        }
        DispatchQueue.main.async {
            withAnimation(.easeInOut(duration: 0.10)) {
                proxy.scrollTo(step.target.rawValue, anchor: .center)
            }
            model.revealTutorialOverlay(for: step.id, after: 0.035)
        }
    }
}

struct TutorialOverlay: View {
    @EnvironmentObject private var model: AppModel
    let anchors: [TutorialTarget: CGRect]

    var body: some View {
        GeometryReader { proxy in
            if let step = model.currentTutorialStep {
                let targetRect = targetRect(for: step.target, proxy: proxy)
                ZStack {
                    if model.tutorialOverlayReady {
                        TutorialDimLayer(targetRect: targetRect)
                            .transition(.opacity)

                        RoundedRectangle(cornerRadius: 12)
                            .fill(.clear)
                            .frame(width: max(80, targetRect.width + 18), height: max(52, targetRect.height + 18))
                            .overlay(
                                RoundedRectangle(cornerRadius: 12)
                                    .stroke(AppTheme.gold, lineWidth: 3)
                                    .shadow(color: AppTheme.gold.opacity(0.8), radius: 14)
                            )
                            .background(
                                RoundedRectangle(cornerRadius: 12)
                                    .fill(AppTheme.gold.opacity(0.12))
                                    .blur(radius: 0.5)
                            )
                            .position(x: targetRect.midX, y: targetRect.midY)
                            .allowsHitTesting(false)

                        TutorialCallout(step: step, targetRect: targetRect, containerSize: proxy.size)
                            .transition(.opacity)
                    } else {
                        TutorialTransitionDimLayer()
                    }
                }
                .ignoresSafeArea()
                .zIndex(1000)
            }
        }
        .allowsHitTesting(model.tutorialActive)
        .animation(.easeOut(duration: 0.06), value: model.tutorialActive)
    }

    private func targetRect(for target: TutorialTarget, proxy: GeometryProxy) -> CGRect {
        if let rect = anchors[target], rect.width > 8, rect.height > 8 {
            return clampedRect(rect, target: target, size: proxy.size)
        }
        return fallbackRect(for: target, size: proxy.size)
    }

    private func clampedRect(_ rect: CGRect, target: TutorialTarget, size: CGSize) -> CGRect {
        guard target == .homeWorkspace else { return rect }
        let margin: CGFloat = 28
        let minX = max(rect.minX, margin)
        let minY = max(rect.minY, margin)
        let maxX = min(rect.maxX, size.width - margin)
        let maxY = min(rect.maxY, size.height - margin)
        return CGRect(
            x: minX,
            y: minY,
            width: max(120, maxX - minX),
            height: max(160, maxY - minY)
        )
    }

    private func fallbackRect(for target: TutorialTarget, size: CGSize) -> CGRect {
        let sidebarWidth = min(440, max(330, size.width * 0.23))
        let detailX = sidebarWidth + 34
        let detailWidth = max(520, size.width - detailX - 34)
        let navX: CGFloat = 54
        let navW = max(250, sidebarWidth - 96)

        switch target {
        case .appShell:
            return CGRect(x: 34, y: 248, width: max(120, sidebarWidth - 70), height: 58)
        case .homeNav:
            return CGRect(x: navX, y: 375, width: navW, height: 56)
        case .driveNav:
            return CGRect(x: navX, y: 428, width: navW, height: 56)
        case .analyzeNav:
            return CGRect(x: navX, y: 481, width: navW, height: 56)
        case .profileNav:
            return CGRect(x: navX, y: 534, width: navW, height: 56)
        case .proVideoNav:
            return CGRect(x: navX, y: 587, width: navW, height: 56)
        case .aiNav:
            return CGRect(x: navX, y: 640, width: navW, height: 56)
        case .setupNav:
            return CGRect(x: navX, y: 693, width: navW, height: 56)
        case .homeWorkspace:
            return CGRect(x: detailX, y: 28, width: detailWidth, height: size.height - 56)
        case .homeHero:
            return CGRect(x: detailX, y: 248, width: detailWidth, height: 340)
        case .coachIdentity:
            return CGRect(x: detailX + detailWidth * 0.34, y: 620, width: detailWidth * 0.3, height: 115)
        case .levelSelect:
            return CGRect(x: detailX, y: 760, width: detailWidth, height: 170)
        case .quickStart:
            return CGRect(x: detailX, y: 980, width: detailWidth * 0.52, height: 210)
        case .sessionState:
            return CGRect(x: detailX + detailWidth * 0.56, y: 980, width: detailWidth * 0.42, height: 210)
        case .driveTrack:
            return CGRect(x: detailX, y: 250, width: detailWidth, height: 160)
        case .driveProReference:
            return CGRect(x: detailX + detailWidth * 0.52, y: 520, width: detailWidth * 0.46, height: 260)
        case .coachOutput:
            return CGRect(x: detailX, y: 800, width: detailWidth, height: max(260, size.height - 830))
        case .analyzeActions:
            return CGRect(x: detailX, y: 390, width: detailWidth, height: 80)
        case .radarPanel:
            return CGRect(x: detailX, y: 480, width: detailWidth, height: 320)
        case .sessionHistory:
            return CGRect(x: navX, y: max(900, size.height - 230), width: navW, height: 160)
        case .curriculum:
            return CGRect(x: detailX + detailWidth * 0.45, y: 300, width: detailWidth * 0.52, height: 420)
        case .aiSettings:
            return CGRect(x: detailX, y: 260, width: detailWidth, height: 360)
        case .proVideoSetup:
            return CGRect(x: detailX, y: 330, width: detailWidth * 0.5, height: 330)
        case .setupCoach:
            return CGRect(x: detailX, y: 330, width: detailWidth, height: 260)
        }
    }
}

struct TutorialTransitionDimLayer: View {
    var body: some View {
        Rectangle()
            .fill(Color.black.opacity(0.58))
            .ignoresSafeArea()
            .transition(.opacity)
    }
}

struct TutorialDimLayer: View {
    let targetRect: CGRect

    var body: some View {
        Rectangle()
            .fill(Color.black.opacity(0.58))
            .ignoresSafeArea()
            .mask(
                Rectangle()
                    .overlay(
                        RoundedRectangle(cornerRadius: 14)
                            .frame(width: max(92, targetRect.width + 24), height: max(64, targetRect.height + 24))
                            .position(x: targetRect.midX, y: targetRect.midY)
                            .blendMode(.destinationOut)
                    )
            )
            .compositingGroup()
    }
}

struct TutorialCallout: View {
    @EnvironmentObject private var model: AppModel
    let step: TutorialStep
    let targetRect: CGRect
    let containerSize: CGSize

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center, spacing: 10) {
                Text(step.eyebrow.uppercased())
                    .font(.caption.weight(.bold))
                    .foregroundStyle(AppTheme.gold)
                    .padding(.vertical, 5)
                    .padding(.horizontal, 8)
                    .background(AppTheme.gold.opacity(0.16), in: Capsule())
                Spacer()
                Text(model.tutorialProgressText)
                    .font(.caption.monospacedDigit().weight(.semibold))
                    .foregroundStyle(.secondary)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text(step.title)
                    .font(.title2.weight(.bold))
                Text(step.body)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            TutorialProgressBar(progress: Double(step.id + 1) / Double(max(model.tutorialSteps.count, 1)))

            HStack(spacing: 10) {
                Button {
                    model.skipTutorial()
                } label: {
                    Label("Skip", systemImage: "xmark")
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
                .buttonStyle(ActionButtonStyle())
                .keyboardShortcut(.cancelAction)

                Spacer()

                Button {
                    model.previousTutorialStep()
                } label: {
                    Label("Back", systemImage: "chevron.left")
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
                .buttonStyle(ActionButtonStyle())
                .disabled(model.tutorialIndex == 0)

                Button {
                    model.nextTutorialStep()
                } label: {
                    Label(model.tutorialIndex + 1 >= model.tutorialSteps.count ? "Done" : "Next", systemImage: model.tutorialIndex + 1 >= model.tutorialSteps.count ? "checkmark" : "chevron.right")
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
                .buttonStyle(ActionButtonStyle(prominent: true))
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: calloutSize.width, alignment: .leading)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .background(AppTheme.panel.opacity(0.9), in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(AppTheme.gold.opacity(0.55), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.35), radius: 24, y: 14)
        .position(calloutPosition)
    }

    private var calloutSize: CGSize {
        let width: CGFloat
        if step.target == .homeWorkspace {
            width = min(430, max(390, containerSize.width * 0.30))
        } else {
            width = min(430, max(340, containerSize.width * 0.34))
        }
        return CGSize(width: width, height: 330)
    }

    private var calloutPosition: CGPoint {
        let margin: CGFloat = 28
        let size = calloutSize
        let safeRect = CGRect(
            x: margin,
            y: margin,
            width: max(1, containerSize.width - margin * 2),
            height: max(1, containerSize.height - margin * 2)
        )
        let targetWithBreathingRoom = targetRect.insetBy(dx: -20, dy: -20)
        let gap: CGFloat = 26

        if step.target == .homeWorkspace {
            return CGPoint(x: safeRect.minX + size.width / 2, y: safeRect.maxY - size.height / 2)
        }

        let right = CGPoint(x: targetRect.maxX + size.width / 2 + gap, y: targetRect.midY)
        let left = CGPoint(x: targetRect.minX - size.width / 2 - gap, y: targetRect.midY)
        let below = CGPoint(x: targetRect.midX, y: targetRect.maxY + size.height / 2 + gap)
        let above = CGPoint(x: targetRect.midX, y: targetRect.minY - size.height / 2 - gap)
        let topLeft = CGPoint(x: safeRect.minX + size.width / 2, y: safeRect.minY + size.height / 2)
        let topRight = CGPoint(x: safeRect.maxX - size.width / 2, y: safeRect.minY + size.height / 2)
        let bottomLeft = CGPoint(x: safeRect.minX + size.width / 2, y: safeRect.maxY - size.height / 2)
        let bottomRight = CGPoint(x: safeRect.maxX - size.width / 2, y: safeRect.maxY - size.height / 2)

        let candidates: [CGPoint]
        if targetRect.midX < containerSize.width * 0.58 {
            candidates = [right, left, below, above, topRight, bottomRight, topLeft, bottomLeft]
        } else {
            candidates = [left, right, below, above, topLeft, bottomLeft, topRight, bottomRight]
        }

        for point in candidates {
            let rect = calloutRect(center: point, size: size)
            if safeRect.contains(rect), !rect.intersects(targetWithBreathingRoom) {
                return point
            }
        }

        return candidates
            .map { clampedCenter($0, size: size, within: safeRect) }
            .min { lhs, rhs in
                let lhsArea = intersectionArea(calloutRect(center: lhs, size: size), targetWithBreathingRoom)
                let rhsArea = intersectionArea(calloutRect(center: rhs, size: size), targetWithBreathingRoom)
                if lhsArea == rhsArea {
                    return distanceSquared(lhs, targetRect.center) > distanceSquared(rhs, targetRect.center)
                }
                return lhsArea < rhsArea
            } ?? CGPoint(x: safeRect.midX, y: safeRect.midY)
    }

    private func calloutRect(center: CGPoint, size: CGSize) -> CGRect {
        CGRect(x: center.x - size.width / 2, y: center.y - size.height / 2, width: size.width, height: size.height)
    }

    private func clampedCenter(_ point: CGPoint, size: CGSize, within rect: CGRect) -> CGPoint {
        CGPoint(
            x: min(max(point.x, rect.minX + size.width / 2), rect.maxX - size.width / 2),
            y: min(max(point.y, rect.minY + size.height / 2), rect.maxY - size.height / 2)
        )
    }

    private func intersectionArea(_ a: CGRect, _ b: CGRect) -> CGFloat {
        let intersection = a.intersection(b)
        guard !intersection.isNull else { return 0 }
        return max(0, intersection.width) * max(0, intersection.height)
    }

    private func distanceSquared(_ a: CGPoint, _ b: CGPoint) -> CGFloat {
        let dx = a.x - b.x
        let dy = a.y - b.y
        return dx * dx + dy * dy
    }
}

private extension CGRect {
    var center: CGPoint {
        CGPoint(x: midX, y: midY)
    }
}

struct TutorialProgressBar: View {
    let progress: Double

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(AppTheme.panelSoft.opacity(0.5))
                Capsule()
                    .fill(LinearGradient(colors: [AppTheme.gold, AppTheme.cyan], startPoint: .leading, endPoint: .trailing))
                    .frame(width: max(12, geometry.size.width * CGFloat(min(max(progress, 0), 1))))
            }
        }
        .frame(height: 7)
    }
}

struct PageHeader: View {
    let title: String
    let subtitle: String
    let icon: String

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            Image(systemName: icon)
                .font(.system(size: 24, weight: .semibold))
                .foregroundStyle(AppTheme.gold)
                .frame(width: 46, height: 46)
                .background(AppTheme.panelSoft, in: RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 30, weight: .bold))
                Text(subtitle)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
    }
}

struct StatusBadge: View {
    let title: String
    let active: Bool
    var activeText = "Online"
    var inactiveText = "Idle"

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(active ? AppTheme.green : Color.gray.opacity(0.55))
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(active ? activeText : inactiveText)
                    .font(.callout.weight(.semibold))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .appPanel()
    }
}

struct InlineStatusRow: View {
    let title: String
    let active: Bool
    var activeText = "Online"
    var inactiveText = "Idle"

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(active ? AppTheme.green : Color.gray.opacity(0.55))
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(active ? activeText : inactiveText)
                    .font(.callout.weight(.semibold))
            }
            Spacer()
        }
        .padding(.vertical, 8)
    }
}

struct MetricTile: View {
    let label: String
    let value: String
    let icon: String
    var tint: Color = AppTheme.cyan

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(tint)
                .frame(width: 34, height: 34)
                .background(AppTheme.panelSoft, in: RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(value)
                    .font(.title3.weight(.bold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            Spacer()
        }
        .appPanel()
    }
}

struct ActionButtonStyle: ButtonStyle {
    var prominent = false
    var danger = false
    @State private var hovering = false
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.callout.weight(.semibold))
            .foregroundStyle(prominent || danger ? Color.black : Color.primary)
            .padding(.horizontal, 14)
            .frame(height: 34)
            .background(background(configuration.isPressed), in: RoundedRectangle(cornerRadius: 8))
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(hovering ? AppTheme.cyan.opacity(0.7) : AppTheme.border, lineWidth: prominent || danger ? 0 : 1)
            )
            .opacity(isEnabled ? 1.0 : 0.45)
            .scaleEffect(configuration.isPressed ? 0.975 : (hovering && isEnabled ? 1.025 : 1.0))
            .shadow(color: hovering && isEnabled ? Color.black.opacity(0.18) : Color.clear, radius: 8, y: 3)
            .animation(.easeOut(duration: 0.16), value: configuration.isPressed)
            .animation(.easeOut(duration: 0.18), value: hovering)
            .onHover { hovering = $0 }
    }

    private func background(_ pressed: Bool) -> Color {
        let base = danger ? AppTheme.red : (prominent ? AppTheme.gold : AppTheme.panelSoft)
        let hoverBoost = hovering ? base.opacity(0.95) : base.opacity(0.82)
        return pressed ? base.opacity(0.64) : hoverBoost
    }
}

struct Sidebar: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 10) {
                    if let image = NSImage(named: "AppIcon") {
                        Image(nsImage: image)
                            .resizable()
                            .frame(width: 34, height: 34)
                            .clipShape(RoundedRectangle(cornerRadius: 7))
                    }
                    Text("ACC AI Coach")
                        .font(.title2.bold())
                        .lineLimit(1)
                        .minimumScaleFactor(0.82)
                }
                Text("Build 6ZA - Tutorial Speech Gate")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text(model.statusMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Button {
                model.startTutorial()
            } label: {
                Label("Tutorial", systemImage: "sparkles")
            }
            .buttonStyle(ActionButtonStyle())
            .tutorialTarget(.appShell)

            List(selection: $model.selectedPage) {
                Section("Coach") {
                    ForEach(AppPage.allCases) { page in
                        Label(page.title, systemImage: page.icon)
                            .tag(page)
                            .tutorialTarget(targetForPage(page))
                    }
                }
            }
            .listStyle(.sidebar)
            .frame(minHeight: 210, maxHeight: 250)

            VStack(spacing: 8) {
                StatusBadge(title: "Rachel", active: model.rachel.running, activeText: "Coaching", inactiveText: "Stopped")
                StatusBadge(title: "Telemetry Helper", active: model.helper.running, activeText: "Forwarding", inactiveText: "Stopped")
                StatusBadge(title: "ACC Game", active: model.accTelemetryLive, activeText: "Running", inactiveText: "Not detected")
            }

            Divider()

            HStack {
                Text("Runs")
                    .font(.headline)
                Text("(\(model.runs.count))")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    model.refreshRuns()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
            }

            Button {
                model.connectProjectFolder()
            } label: {
                Label("Connect Project Folder", systemImage: "folder.badge.gearshape")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(ActionButtonStyle())

            Text(model.projectDataStatus)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(3)

            List(selection: $model.selectedRun) {
                if model.runs.isEmpty {
                    Text(model.runListStatus)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(model.runs) { run in
                        RunRow(run: run)
                        .tag(run)
                    }
                }
            }
            .listStyle(.sidebar)
        }
        .padding()
        .onAppear {
            model.refreshRuns()
            model.refreshExternalAppStatus()
        }
        .onReceive(Timer.publish(every: 2.0, on: .main, in: .common).autoconnect()) { _ in
            model.refreshExternalAppStatus()
        }
    }

    func targetForPage(_ page: AppPage) -> TutorialTarget {
        switch page {
        case .home: return .homeNav
        case .drive: return .driveNav
        case .analyze: return .analyzeNav
        case .profile: return .profileNav
        case .proVideo: return .proVideoNav
        case .ai: return .aiNav
        case .setup: return .setupNav
        case .settings: return .appShell
        }
    }

}

struct RunRow: View {
    let run: RunInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(run.name)
                .font(.callout.weight(.medium))
                .lineLimit(1)
            HStack(spacing: 6) {
                Text("\(run.packets) packets")
                Text(run.track)
                Text(run.level)
                if let score = run.score {
                    Text("\(score)")
                        .foregroundStyle(AppTheme.gold)
                }
                if run.hasDecision { Image(systemName: "checkmark.seal.fill").foregroundStyle(AppTheme.green) }
                if run.hasProfile { Image(systemName: "person.crop.circle.badge.checkmark").foregroundStyle(AppTheme.cyan) }
                if run.hasReplay { Image(systemName: "doc.text.fill").foregroundStyle(AppTheme.gold) }
                if run.hasSessionSummary { Image(systemName: "chart.bar.fill").foregroundStyle(AppTheme.yellow) }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 3)
    }
}

struct DashboardHero: View {
    @EnvironmentObject private var model: AppModel

    var latestScoredRun: RunInfo? {
        model.runs.first { $0.score != nil }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    Image(systemName: "flag.checkered.2.crossed")
                        .font(.system(size: 28, weight: .semibold))
                        .foregroundStyle(AppTheme.gold)
                        .frame(width: 56, height: 56)
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Today")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text(nextFocusTitle)
                            .font(.system(size: 28, weight: .bold))
                            .lineLimit(2)
                    }
                }
                Text(nextFocusBody)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button { model.selectedPage = .drive } label: {
                        Label("Start Drive", systemImage: "play.fill")
                    }
                    .buttonStyle(ActionButtonStyle(prominent: true))
                    Button { model.selectedPage = .analyze } label: {
                        Label("Review Sessions", systemImage: "chart.xyaxis.line")
                    }
                    .buttonStyle(ActionButtonStyle())
                    Button { model.startTutorial() } label: {
                        Label("Tutorial", systemImage: "sparkles")
                    }
                    .buttonStyle(ActionButtonStyle())
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            VStack(alignment: .leading, spacing: 10) {
                Text("Latest Score")
                    .font(.headline)
                Text(latestScoredRun?.score.map { "\($0)" } ?? "--")
                    .font(.system(size: 52, weight: .bold, design: .rounded))
                    .foregroundStyle(scoreTint(latestScoredRun?.score))
                    .contentTransition(.numericText())
                Text(latestScoredRun?.name ?? "No scored session yet")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            .frame(width: 220, alignment: .leading)
        }
        .appPanel()
        .tutorialTarget(.homeHero)
    }

    var nextFocusTitle: String {
        guard let first = model.sessionAnalysis?.recommendations.first else {
            return "Build the next clean Spa session"
        }
        return first.components(separatedBy: ":").first ?? "Review the highest-value correction"
    }

    var nextFocusBody: String {
        guard let first = model.sessionAnalysis?.recommendations.first else {
            return "Run a session, stop coaching, then review the generated radar, session summary, and curriculum focus."
        }
        return first
    }

    func scoreTint(_ score: Int?) -> Color {
        guard let score else { return .secondary }
        if score >= 82 { return AppTheme.green }
        if score >= 65 { return AppTheme.yellow }
        return AppTheme.red
    }
}

struct SessionHistoryPanel: View {
    @EnvironmentObject private var model: AppModel
    var limit = 6

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Session History")
                    .font(.headline)
                Spacer()
                Button { model.refreshRuns() } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .buttonStyle(ActionButtonStyle())
            }
            if model.runs.isEmpty {
                Text(model.runListStatus)
                    .foregroundStyle(.secondary)
            } else {
                LazyVStack(spacing: 10) {
                    ForEach(Array(model.runs.prefix(limit))) { run in
                        SessionCard(run: run, selected: model.selectedRun?.name == run.name)
                            .onTapGesture {
                                model.selectedRun = run
                                model.loadSessionAnalysis(for: run.name)
                                model.reportText = run.hasSessionSummary ? "Loaded \(run.name). Open a report or show the radar." : "Loaded \(run.name). Generate Session Summary to score this run."
                            }
                    }
                }
            }
        }
        .appPanel()
        .tutorialTarget(.sessionHistory)
    }
}

struct SessionCard: View {
    let run: RunInfo
    let selected: Bool
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .stroke(scoreColor.opacity(0.25), lineWidth: 6)
                Circle()
                    .trim(from: 0, to: CGFloat((run.score ?? 0)) / 100)
                    .stroke(scoreColor, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Text(run.score.map { "\($0)" } ?? "--")
                    .font(.caption.weight(.bold))
            }
            .frame(width: 46, height: 46)

            VStack(alignment: .leading, spacing: 4) {
                Text(displayName)
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                HStack(spacing: 8) {
                    Label(run.track, systemImage: "map")
                    Label(run.level, systemImage: "dial.medium")
                    Label("\(run.packets)", systemImage: "dot.radiowaves.left.and.right")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: selected ? "checkmark.circle.fill" : "chevron.right")
                .foregroundStyle(selected ? AppTheme.green : .secondary)
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
        .background(selected ? AppTheme.cyan.opacity(0.14) : AppTheme.panelSoft.opacity(hovering ? 0.82 : 0.42), in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(selected ? AppTheme.cyan : (hovering ? AppTheme.gold.opacity(0.55) : AppTheme.border), lineWidth: 1)
        )
        .scaleEffect(hovering ? 1.01 : 1.0)
        .animation(.easeOut(duration: 0.16), value: hovering)
        .onHover { hovering = $0 }
        .help("Open \(run.name)")
    }

    var displayName: String {
        run.name.replacingOccurrences(of: "-", with: " ")
    }

    var scoreColor: Color {
        guard let score = run.score else { return .secondary }
        if score >= 82 { return AppTheme.green }
        if score >= 65 { return AppTheme.yellow }
        return AppTheme.red
    }
}

struct CurriculumPanel: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Driver Curriculum")
                    .font(.headline)
                Spacer()
                Text(model.selectedMode)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(AppTheme.gold)
            }
            CurriculumStep(
                index: 0,
                title: "1. Valid Lap Control",
                status: statusFor(index: 0),
                text: "Keep laps valid before chasing reference deltas. Major invalid events override tiny mistakes.",
                selected: model.selectedCurriculumStep == 0
            )
            CurriculumStep(
                index: 1,
                title: "2. Rotation Before Throttle",
                status: statusFor(index: 1),
                text: "Wait until steering starts to open, then build power progressively.",
                selected: model.selectedCurriculumStep == 1
            )
            CurriculumStep(
                index: 2,
                title: "3. Brake Release Timing",
                status: statusFor(index: 2),
                text: "Avoid abrupt release. Carry light trail brake only when the car needs rotation.",
                selected: model.selectedCurriculumStep == 2
            )
            CurriculumStep(
                index: 3,
                title: "4. Reference Delta Work",
                status: "Prepared",
                text: "Use saved session examples to compare where time was lost and which correction matters first.",
                selected: model.selectedCurriculumStep == 3
            )
            CurriculumDetailCard(index: model.selectedCurriculumStep, status: statusFor(index: model.selectedCurriculumStep))
        }
        .appPanel()
        .tutorialTarget(.curriculum)
    }

    func statusFor(index: Int) -> String {
        guard let analysis = model.sessionAnalysis else { return index == 0 ? "Active" : "Prepared" }
        if index == 0 && analysis.invalidLaps > 0 { return "Active" }
        if index == 1 && (analysis.scores.first { $0.0 == "Rotation" }?.1 ?? 100) < 70 { return "Active" }
        if index == 2 && (analysis.scores.first { $0.0 == "Braking" }?.1 ?? 100) < 75 { return "Active" }
        return index == 0 ? "Stable" : "Prepared"
    }
}

struct CurriculumStep: View {
    @EnvironmentObject private var model: AppModel
    let index: Int
    let title: String
    let status: String
    let text: String
    let selected: Bool
    @State private var hovering = false

    var body: some View {
        Button {
            model.selectedCurriculumStep = index
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Circle()
                    .fill(tint)
                    .frame(width: 10, height: 10)
                    .padding(.top, 5)
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(title)
                            .font(.callout.weight(.semibold))
                        Spacer()
                        Text(status)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(tint)
                        Image(systemName: "chevron.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(selected ? tint : .secondary.opacity(0.6))
                    }
                    Text(text)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .buttonStyle(.plain)
        .padding(10)
        .background(selected ? tint.opacity(0.13) : AppTheme.panelSoft.opacity(hovering ? 0.66 : 0.46), in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(selected ? tint.opacity(0.95) : (hovering ? AppTheme.cyan.opacity(0.55) : AppTheme.border), lineWidth: 1)
        )
        .scaleEffect(hovering ? 1.008 : 1.0)
        .animation(.easeOut(duration: 0.16), value: hovering)
        .animation(.easeOut(duration: 0.16), value: selected)
        .onHover { hovering = $0 }
        .help("Open curriculum detail")
    }

    var tint: Color {
        if status == "Active" { return AppTheme.gold }
        if status == "Stable" { return AppTheme.green }
        return AppTheme.yellow
    }
}

struct CurriculumDetailCard: View {
    @EnvironmentObject private var model: AppModel
    let index: Int
    let status: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Selected Focus")
                    .font(.headline)
                Spacer()
                Text(status)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(tint)
            }
            Text(title)
                .font(.title3.weight(.bold))
            Text(reason)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Divider()
            VStack(alignment: .leading, spacing: 6) {
                Label(passCriteria, systemImage: "checkmark.circle")
                Label(nextDrill, systemImage: "flag.checkered")
            }
            .font(.callout)
            .foregroundStyle(.primary)
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(tint.opacity(0.45), lineWidth: 1))
    }

    var title: String {
        switch index {
        case 1: return "Rotation Before Throttle"
        case 2: return "Brake Release Timing"
        case 3: return "Reference Delta Work"
        default: return "Valid Lap Control"
        }
    }

    var reason: String {
        guard let analysis = model.sessionAnalysis else {
            return "No scored session is selected yet, so this curriculum step is prepared but not personalized."
        }
        switch index {
        case 1:
            let score = analysis.scores.first { $0.0 == "Rotation" }?.1 ?? 0
            return "Rotation is \(score)/100. This becomes active when telemetry shows throttle while the steering is still loaded, which usually means the front is washing wide."
        case 2:
            let score = analysis.scores.first { $0.0 == "Braking" }?.1 ?? 0
            return "Braking is \(score)/100. This becomes active when early braking, abrupt release, short trail braking, or pedal overlap is costing corner entry and rotation."
        case 3:
            return "Reference work uses your strongest lap as the local reference. It should come after the car is stable enough that deltas are meaningful."
        default:
            return "This session has \(analysis.invalidLaps) invalid lap(s). If invalid laps or major incidents appear, Rachel prioritizes keeping the lap valid before smaller technique gains."
        }
    }

    var passCriteria: String {
        switch index {
        case 1: return "Pass when rotation score stays above 70 across multiple sessions."
        case 2: return "Pass when braking score stays above 75 and pedal overlap is rare."
        case 3: return "Pass when valid laps are consistent enough to compare deltas corner by corner."
        default: return "Pass when most completed laps are valid and no major incident dominates the session."
        }
    }

    var nextDrill: String {
        switch index {
        case 1: return "Next drill: delay throttle until the wheel starts opening on the selected weak corner."
        case 2: return "Next drill: make one smooth brake release instead of jumping off the pedal."
        case 3: return "Next drill: compare one corner against your best lap and change only one input."
        default: return "Next drill: leave margin at the invalidation corner and finish a clean lap first."
        }
    }

    var tint: Color {
        if status == "Active" { return AppTheme.gold }
        if status == "Stable" { return AppTheme.green }
        return AppTheme.yellow
    }
}

struct HomeView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TutorialPageScrollView(page: .home) {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: "ACC AI Coach", subtitle: "Spa beginner coaching workspace", icon: "flag.checkered")

                DashboardHero()

                HStack(spacing: 14) {
                    MetricTile(label: "Track", value: model.detectedTrack, icon: "map", tint: AppTheme.gold)
                    MetricTile(label: "Coach", value: "Rachel", icon: "speaker.wave.2.fill", tint: AppTheme.cyan)
                        .tutorialTarget(.coachIdentity)
                    MetricTile(label: "Engine", value: model.openAIEnabled ? "Hybrid" : "Local", icon: "cpu", tint: model.openAIEnabled ? AppTheme.green : AppTheme.gold)
                }

                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text("Choose Level")
                            .font(.headline)
                        Spacer()
                        LevelPicker()
                    }

                    HStack(alignment: .top, spacing: 12) {
                        CoachLevelCard(
                            title: "Beginner",
                            subtitle: "Prepared",
                            text: "Valid laps, stable car control, clear turn-number coaching, and one correction at a time.",
                            active: model.selectedMode == "Beginner",
                            statusTint: AppTheme.green
                        )
                        CoachLevelCard(
                            title: "Intermediate",
                            subtitle: "Prepared",
                            text: "Trail-brake detail, throttle pickup timing, and deeper reference deltas.",
                            active: model.selectedMode == "Intermediate",
                            statusTint: AppTheme.yellow
                        )
                        CoachLevelCard(
                            title: "Pro",
                            subtitle: "Prepared",
                            text: "Aggressive lap-time hunting, setup separation, and pro-reference analysis.",
                            active: model.selectedMode == "Pro",
                            statusTint: AppTheme.yellow
                        )
                    }
                }
                .tutorialTarget(.levelSelect)

                HStack(alignment: .top, spacing: 14) {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Quick Start")
                            .font(.headline)
                        TextField("Run name", text: $model.runName)
                            .textFieldStyle(.roundedBorder)
                        HStack {
                            Button { model.testVoice() } label: {
                                Label("Test Voice", systemImage: "speaker.wave.2.fill")
                            }
                            .buttonStyle(ActionButtonStyle())

                            Button { model.startTutorial() } label: {
                                Label("Tutorial", systemImage: "sparkles")
                            }
                            .buttonStyle(ActionButtonStyle())

                            Button { model.startDriving() } label: {
                                Label("Start Coaching", systemImage: "play.fill")
                            }
                            .buttonStyle(ActionButtonStyle(prominent: true))
                            .disabled(model.rachel.running)

                            Button(role: .destructive) { model.stopDriving() } label: {
                                Label("Stop", systemImage: "stop.fill")
                            }
                            .buttonStyle(ActionButtonStyle(danger: true))
                            .disabled(!model.rachel.running && !model.helper.running)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .appPanel()
                    .tutorialTarget(.quickStart)

                    VStack(alignment: .leading, spacing: 12) {
                        Text("Session State")
                            .font(.headline)
                        InlineStatusRow(title: "Rachel", active: model.rachel.running, activeText: "Speaking and analyzing", inactiveText: "Ready")
                        Divider()
                        InlineStatusRow(title: "Telemetry Helper", active: model.helper.running, activeText: "Forwarding telemetry", inactiveText: "Ready")
                        Divider()
                        InlineStatusRow(title: "ACC Game", active: model.accTelemetryLive, activeText: "Running", inactiveText: "Not detected")
                    }
                    .frame(width: 320)
                    .appPanel()
                    .tutorialTarget(.sessionState)
                }

                HStack(alignment: .top, spacing: 14) {
                    SessionHistoryPanel(limit: 4)
                    CurriculumPanel()
                }

                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text("Latest Coach Feed")
                            .font(.headline)
                        Spacer()
                        Button { model.selectedPage = .drive } label: {
                            Label("Open Drive", systemImage: "arrow.right")
                        }
                        .buttonStyle(ActionButtonStyle())
                    }
                    Text((model.rachel.lines + model.helper.lines).suffix(35).joined(separator: "\n"))
                        .font(.system(.body, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.9))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .padding(14)
                        .background(Color.black.opacity(0.42), in: RoundedRectangle(cornerRadius: 8))
                }
                .appPanel()
            }
            .padding(24)
            .tutorialTarget(.homeWorkspace)
        }
        .onAppear {
            if let run = model.selectedRun {
                model.loadSessionAnalysis(for: run.name)
            }
        }
        .onChange(of: model.selectedRun) { _, run in
            if let run {
                model.loadSessionAnalysis(for: run.name)
            }
        }
    }
}

struct CoachLevelCard: View {
    @EnvironmentObject private var model: AppModel
    let title: String
    let subtitle: String
    let text: String
    let active: Bool
    let statusTint: Color
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(title)
                    .font(.headline)
                Spacer()
                Text(subtitle)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(active ? statusTint : statusTint.opacity(0.82))
            }
            Text(text)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
        .background(active ? AppTheme.panelSoft : AppTheme.panel.opacity(0.58), in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(active ? statusTint.opacity(0.9) : (hovering ? AppTheme.cyan.opacity(0.55) : AppTheme.border), lineWidth: 1)
        )
        .scaleEffect(hovering ? 1.012 : 1.0)
        .animation(.easeOut(duration: 0.18), value: hovering)
        .onHover { hovering = $0 }
        .onTapGesture {
            model.selectedMode = title
            model.refreshRunName()
        }
    }
}

struct LevelPicker: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Picker("Level", selection: $model.selectedMode) {
            Text("Beginner").tag("Beginner")
            Text("Intermediate").tag("Intermediate")
            Text("Pro").tag("Pro")
        }
        .pickerStyle(.segmented)
        .frame(width: 360)
        .onChange(of: model.selectedMode) { _, _ in
            model.refreshRunName()
        }
    }
}

struct TrackSelector: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Track")
                    .font(.headline)
                Spacer()
                Picker("Track mode", selection: $model.trackMode) {
                    Text("Auto Detect").tag("Auto Detect")
                    Text("Manual").tag("Manual")
                }
                .pickerStyle(.segmented)
                .frame(width: 240)
            }
            HStack(spacing: 12) {
                HStack(spacing: 10) {
                    Image(systemName: "location.magnifyingglass")
                        .foregroundStyle(AppTheme.cyan)
                        .frame(width: 30, height: 30)
                        .background(AppTheme.panelSoft, in: RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Detected")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(model.detectedTrack.isEmpty ? "Waiting" : model.detectedTrack)
                            .font(.headline)
                    }
                    Spacer()
                }
                .padding(12)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(AppTheme.border, lineWidth: 1))

                if model.trackMode == "Manual" {
                    Picker("Manual fallback", selection: $model.manualTrack) {
                        Text("Spa").tag("Spa")
                    }
                    .pickerStyle(.menu)
                    .frame(width: 180)
                    .onChange(of: model.manualTrack) { _, _ in model.refreshRunName() }
                }
            }
            Text(model.trackMode == "Auto Detect" ? "Telemetry track name is used after packets arrive. Spa is the current fallback." : "Manual fallback is used when telemetry track detection is unavailable.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .onChange(of: model.trackMode) { _, _ in model.refreshRunName() }
    }
}

struct DriveView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TutorialPageScrollView(page: .drive) {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: "Drive", subtitle: "\(model.trackMode == "Auto Detect" ? "Auto track detection" : "Manual track") · \(model.selectedMode)", icon: "steeringwheel")

                HStack(spacing: 14) {
                    MetricTile(label: "Mode", value: model.selectedMode, icon: "dial.medium", tint: AppTheme.gold)
                    MetricTile(label: "Track", value: model.detectedTrack, icon: "map", tint: AppTheme.cyan)
                    MetricTile(label: "Analysis", value: model.isAnalyzing ? "Building" : "Ready", icon: "waveform.path.ecg", tint: model.isAnalyzing ? AppTheme.yellow : AppTheme.green)
                    MetricTile(label: "AI", value: model.openAIEnabled ? "Enabled" : "Local", icon: "brain.head.profile", tint: model.openAIEnabled ? AppTheme.green : AppTheme.gold)
                }

                HStack(alignment: .top, spacing: 14) {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Driving Level")
                                .font(.headline)
                            Spacer()
                            LevelPicker()
                        }
                        HStack(alignment: .top, spacing: 12) {
                            CoachLevelCard(title: "Beginner", subtitle: "Prepared", text: "Clear fundamentals, valid laps, and one high-value correction at a time.", active: model.selectedMode == "Beginner", statusTint: AppTheme.green)
                            CoachLevelCard(title: "Intermediate", subtitle: "Prepared", text: "More detailed input timing and reference deltas when enabled.", active: model.selectedMode == "Intermediate", statusTint: AppTheme.yellow)
                            CoachLevelCard(title: "Pro", subtitle: "Prepared", text: "Sharper lap-time comparison and setup separation later.", active: model.selectedMode == "Pro", statusTint: AppTheme.yellow)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .appPanel()
                    .tutorialTarget(.levelSelect)
                }

                TrackSelector()
                    .appPanel()
                    .tutorialTarget(.driveTrack)

                HStack(alignment: .top, spacing: 14) {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Session Control")
                            .font(.headline)
                        TextField("Run name", text: $model.runName)
                            .textFieldStyle(.roundedBorder)
                        HStack {
                            Button {
                                model.refreshRunName()
                            } label: {
                                Label("Auto Name", systemImage: "text.badge.plus")
                            }
                            .buttonStyle(ActionButtonStyle())

                            Button {
                                model.testVoice()
                            } label: {
                                Label("Test Voice", systemImage: "speaker.wave.2.fill")
                            }
                            .buttonStyle(ActionButtonStyle())

                            Button {
                                model.startDriving()
                            } label: {
                                Label("Run", systemImage: "play.fill")
                            }
                            .buttonStyle(ActionButtonStyle(prominent: true))
                            .disabled(model.rachel.running)

                            Button(role: .destructive) {
                                model.stopDriving()
                            } label: {
                                Label("Stop", systemImage: "stop.fill")
                            }
                            .buttonStyle(ActionButtonStyle(danger: true))
                            .disabled(!model.rachel.running && !model.helper.running)
                        }
                    }
                    .appPanel()
                    .tutorialTarget(.quickStart)

                    VStack(alignment: .leading, spacing: 12) {
                        Text("Coach Mode")
                            .font(.headline)
                        Text(model.selectedMode)
                            .font(.system(size: 34, weight: .bold))
                        Text(modeText(model.selectedMode))
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Divider()
                        Toggle(isOn: $model.useProVideoReference) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Use Pro Video Reference")
                                    .font(.headline)
                                Text("Compare your lap against the latest analyzed pro-video HUD trace.")
                                    .font(.callout)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .toggleStyle(.switch)
                        Button { model.selectedPage = .proVideo } label: {
                            Label("Open Pro Video Setup", systemImage: "video.badge.waveform")
                        }
                        .buttonStyle(ActionButtonStyle())
                        Divider()
                        Text("Stop automatically builds replay, reference comparison, driver profile, and session score.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .appPanel()
                    .tutorialTarget(.driveProReference)
                }

                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text("Rachel Output")
                            .font(.headline)
                        Spacer()
                        Text(model.rachel.running ? "Live" : (model.isAnalyzing ? "Analyzing" : "Standby"))
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(model.rachel.running ? AppTheme.green : (model.isAnalyzing ? AppTheme.yellow : .secondary))
                    }
                    ScrollView {
                        Text((model.rachel.lines + model.helper.lines).suffix(120).joined(separator: "\n"))
                            .font(.system(.body, design: .monospaced))
                            .foregroundStyle(Color.white.opacity(0.92))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                    .frame(minHeight: 320)
                    .padding(14)
                    .background(Color.black.opacity(0.42), in: RoundedRectangle(cornerRadius: 8))
                }
                .appPanel()
                .tutorialTarget(.coachOutput)
            }
            .padding(24)
        }
    }

    func modeText(_ mode: String) -> String {
        switch mode {
        case "Intermediate":
            return "Prepared for later: more detailed trail-brake, throttle pickup, and reference comparison feedback."
        case "Pro":
            return "Prepared for later: aggressive delta hunting, setup separation, and pro-reference analysis."
        default:
            return "Active now: stability, valid laps, official invalid detection, and simple corner-specific corrections."
        }
    }
}

struct AnalyzeView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TutorialPageScrollView(page: .analyze) {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: "Analyze", subtitle: model.selectedRun?.name ?? "Select a run", icon: "waveform.path.ecg.rectangle")
                HStack(spacing: 14) {
                    MetricTile(label: "Packets", value: "\(model.selectedRun?.packets ?? 0)", icon: "dot.radiowaves.left.and.right")
                    MetricTile(label: "Score", value: model.selectedRun?.score.map { "\($0)" } ?? "Pending", icon: "chart.bar.fill", tint: model.selectedRun?.score == nil ? AppTheme.yellow : AppTheme.green)
                    MetricTile(label: "Track", value: model.selectedRun?.track ?? "Unknown", icon: "map", tint: AppTheme.cyan)
                    MetricTile(label: "Decision", value: model.selectedRun?.hasDecision == true ? "Ready" : "Missing", icon: "checkmark.seal", tint: model.selectedRun?.hasDecision == true ? AppTheme.green : AppTheme.gold)
                }
                AnalysisSummaryStrip()
                HStack {
                    Button { model.generateReplay() } label: { Label("Replay", systemImage: "arrow.triangle.2.circlepath") }
                        .buttonStyle(ActionButtonStyle())
                    Button { model.validate() } label: { Label("Validate", systemImage: "checkmark.shield") }
                        .buttonStyle(ActionButtonStyle())
                    Button { model.updateProfile() } label: { Label("Profile", systemImage: "person.crop.circle.badge.checkmark") }
                        .buttonStyle(ActionButtonStyle())
                    Button { model.generateAICoach() } label: { Label("AI Coach", systemImage: "brain.head.profile") }
                        .buttonStyle(ActionButtonStyle(prominent: true))
                    Button { model.generateSessionSummary() } label: { Label("Session Summary", systemImage: "chart.bar.fill") }
                        .buttonStyle(ActionButtonStyle(prominent: true))
                    Button { model.openRadar() } label: { Label("Show Radar", systemImage: "hexagon") }
                        .buttonStyle(ActionButtonStyle())
                    Spacer()
                }
                .appPanel()
                .tutorialTarget(.analyzeActions)

                RadarAnalysisPanel()
                SessionHistoryPanel(limit: 5)

                HStack {
                    ReportButtons()
                    Spacer()
                }
                .appPanel()
                ReportText()
                    .frame(minHeight: 360)
            }
            .padding(24)
        }
        .onAppear {
            if let run = model.selectedRun {
                model.loadSessionAnalysis(for: run.name)
            }
        }
        .onChange(of: model.selectedRun) { _, run in
            if let run {
                model.loadSessionAnalysis(for: run.name)
            }
        }
    }
}

struct AnalysisSummaryStrip: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Session Snapshot")
                    .font(.headline)
                if let analysis = model.sessionAnalysis {
                    ViewThatFits(in: .horizontal) {
                        snapshotRow(analysis)
                        VStack(alignment: .leading, spacing: 8) {
                            HStack(spacing: 8) {
                                SnapshotPill(label: "Completed", value: "\(analysis.completedLaps)")
                                SnapshotPill(label: "Valid", value: "\(analysis.validLaps)")
                                SnapshotPill(label: "Invalid", value: "\(analysis.invalidLaps)")
                            }
                            HStack(spacing: 8) {
                                SnapshotPill(label: "Incomplete", value: "\(analysis.incompleteLaps)")
                                SnapshotPill(label: "Best", value: analysis.bestLap)
                            }
                        }
                    }
                } else {
                    Text("Generate Session Summary to populate session scoring and lap details.")
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            VStack(alignment: .leading, spacing: 8) {
                Text("Weakest Area")
                    .font(.headline)
                Text(model.sessionAnalysis?.weakest.last.map { "\($0.0) · \($0.1)/100" } ?? "Pending")
                    .font(.title3.weight(.bold))
                    .foregroundStyle(AppTheme.gold)
                Text(model.sessionAnalysis?.recommendations.first ?? "Select a scored run to see the next correction.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .appPanel()
    }

    private func snapshotRow(_ analysis: SessionAnalysis) -> some View {
        HStack(spacing: 8) {
            SnapshotPill(label: "Completed", value: "\(analysis.completedLaps)")
            SnapshotPill(label: "Valid", value: "\(analysis.validLaps)")
            SnapshotPill(label: "Invalid", value: "\(analysis.invalidLaps)")
            SnapshotPill(label: "Incomplete", value: "\(analysis.incompleteLaps)")
            SnapshotPill(label: "Best", value: analysis.bestLap)
        }
    }
}

struct SnapshotPill: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.callout.weight(.bold))
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 10)
        .frame(minWidth: 82, alignment: .leading)
        .background(AppTheme.panelSoft.opacity(0.52), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct RadarAnalysisPanel: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Performance Radar")
                        .font(.headline)
                    Spacer()
                    Text(model.sessionAnalysis.map { "\($0.overallScore)/100" } ?? "No score")
                        .font(.title3.weight(.bold))
                        .foregroundStyle(AppTheme.gold)
                }
                if let analysis = model.sessionAnalysis {
                    RadarChart(scores: analysis.scores)
                        .frame(width: 310, height: 280)
                } else {
                    VStack(spacing: 12) {
                        Image(systemName: "chart.bar.fill")
                            .font(.system(size: 42, weight: .semibold))
                            .foregroundStyle(AppTheme.gold)
                        Text("Generate Session Summary to show the radar inside the app.")
                            .foregroundStyle(.secondary)
                    }
                    .frame(width: 310, height: 280)
                }
            }
            .frame(width: 350)

            VStack(alignment: .leading, spacing: 14) {
                Text("Score Explanation")
                    .font(.headline)
                if let analysis = model.sessionAnalysis {
                    ForEach(analysis.scores, id: \.0) { item in
                        ScoreExplanationRow(name: item.0, score: item.1)
                    }
                    Divider()
                    Text("Prioritized Recommendations")
                        .font(.headline)
                    ForEach(Array(analysis.recommendations.prefix(3).enumerated()), id: \.offset) { index, text in
                        Text("\(index + 1). \(text)")
                            .font(.callout)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    Text("This panel will explain each score after the summary is generated. It stays inside the app and does not open the browser.")
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .appPanel()
        .tutorialTarget(.radarPanel)
    }
}

struct ScoreExplanationRow: View {
    let name: String
    let score: Int

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Text("\(score)")
                .font(.headline.monospacedDigit())
                .foregroundStyle(scoreColor)
                .frame(width: 38, alignment: .trailing)
            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                    .font(.callout.weight(.semibold))
                Text(explanation)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    var scoreColor: Color {
        if score >= 82 { return AppTheme.green }
        if score >= 65 { return AppTheme.yellow }
        return AppTheme.red
    }

    var explanation: String {
        switch name {
        case "Telemetry":
            return score >= 85 ? "Packet rate and captured fields were stable enough for analysis." : "Telemetry quality was limited, so confidence is lower."
        case "Consistency":
            return score >= 80 ? "Most completed laps were usable and stable." : "Invalid laps or inconsistent completion reduced the score."
        case "Braking":
            return score >= 80 ? "Brake timing and release were close enough to the reference." : "Early braking, short trail braking, or pedal overlap cost time."
        case "Rotation":
            return score >= 80 ? "The car rotated cleanly before throttle in most key zones." : "Throttle while steering loaded suggests understeer or push."
        case "Throttle":
            return score >= 80 ? "Throttle pickup was decisive without overloading the front." : "Late throttle, early throttle, or overlap reduced exit quality."
        case "Stability":
            return score >= 80 ? "No major spins, stops, or invalid-style events dominated the session." : "Major slides, stops, invalidations, or recovery events reduced confidence."
        default:
            return "Score is derived from telemetry, lap validity, incidents, and reference comparison."
        }
    }
}

struct RadarChart: View {
    let scores: [(String, Int)]

    var body: some View {
        GeometryReader { geometry in
            let size = min(geometry.size.width, geometry.size.height)
            let center = CGPoint(x: geometry.size.width / 2, y: geometry.size.height / 2)
            let radius = size * 0.34
            ZStack {
                ForEach([0.25, 0.5, 0.75, 1.0], id: \.self) { scale in
                    PolygonShape(points: polygonPoints(center: center, radius: radius * scale))
                        .stroke(AppTheme.border, lineWidth: 1)
                }
                ForEach(scores.indices, id: \.self) { index in
                    let endpoint = axisPoint(index: index, center: center, radius: radius)
                    Path { path in
                        path.move(to: center)
                        path.addLine(to: endpoint)
                    }
                    .stroke(AppTheme.border.opacity(0.75), lineWidth: 1)
                }
                PolygonShape(points: scorePoints(center: center, radius: radius))
                    .fill(AppTheme.cyan.opacity(0.28))
                PolygonShape(points: scorePoints(center: center, radius: radius))
                    .stroke(AppTheme.cyan, lineWidth: 3)
                ForEach(scores.indices, id: \.self) { index in
                    let point = scorePoint(index: index, center: center, radius: radius)
                    Circle()
                        .fill(AppTheme.gold)
                        .frame(width: 8, height: 8)
                        .position(point)
                }
                ForEach(scores.indices, id: \.self) { index in
                    let point = axisPoint(index: index, center: center, radius: radius + 34)
                    VStack(spacing: 1) {
                        Text(scores[index].0)
                            .font(.caption2.weight(.semibold))
                        Text("\(scores[index].1)")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    .position(point)
                }
            }
        }
    }

    func angle(_ index: Int) -> Double {
        -Double.pi / 2 + Double(index) * 2 * Double.pi / Double(max(scores.count, 1))
    }

    func axisPoint(index: Int, center: CGPoint, radius: CGFloat) -> CGPoint {
        let angle = angle(index)
        return CGPoint(x: center.x + cos(angle) * radius, y: center.y + sin(angle) * radius)
    }

    func scorePoint(index: Int, center: CGPoint, radius: CGFloat) -> CGPoint {
        let scale = CGFloat(scores[index].1) / 100
        return axisPoint(index: index, center: center, radius: radius * scale)
    }

    func polygonPoints(center: CGPoint, radius: CGFloat) -> [CGPoint] {
        scores.indices.map { axisPoint(index: $0, center: center, radius: radius) }
    }

    func scorePoints(center: CGPoint, radius: CGFloat) -> [CGPoint] {
        scores.indices.map { scorePoint(index: $0, center: center, radius: radius) }
    }
}

struct PolygonShape: Shape {
    let points: [CGPoint]

    func path(in rect: CGRect) -> Path {
        var path = Path()
        guard let first = points.first else { return path }
        path.move(to: first)
        for point in points.dropFirst() {
            path.addLine(to: point)
        }
        path.closeSubpath()
        return path
    }
}

struct ProfileView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TutorialPageScrollView(page: .profile) {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: "Profile", subtitle: "Driver memory and curriculum", icon: "person.text.rectangle")
                HStack(spacing: 14) {
                    MetricTile(label: "Driver", value: "F1SH", icon: "person.crop.circle", tint: AppTheme.gold)
                    MetricTile(label: "Track", value: model.selectedRun?.track ?? model.detectedTrack, icon: "map", tint: AppTheme.cyan)
                    MetricTile(label: "Model", value: model.openAIEnabled ? "Hybrid" : "Local", icon: "cpu", tint: model.openAIEnabled ? AppTheme.green : AppTheme.gold)
                    MetricTile(label: "Level", value: model.selectedMode, icon: "dial.medium", tint: AppTheme.green)
                }
                HStack(alignment: .top, spacing: 14) {
                    VStack(spacing: 14) {
                        ProgressOverview()
                        SessionHistoryPanel(limit: 4)
                    }
                    .frame(width: 430)
                    CurriculumPanel()
                }
                HStack {
                    Button { model.openReport("driver_profile_update.md") } label: {
                        Label("Open Profile Report", systemImage: "doc.text")
                    }
                    .buttonStyle(ActionButtonStyle())
                    Button { model.updateProfile() } label: {
                        Label("Update Profile", systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(ActionButtonStyle(prominent: true))
                    Spacer()
                }
                .appPanel()
                ReportText()
                    .frame(minHeight: 260)
            }
            .padding(24)
        }
    }
}

struct ProgressOverview: View {
    @EnvironmentObject private var model: AppModel

    var scoredRuns: [RunInfo] {
        Array(model.runs.filter { $0.score != nil }.prefix(6)).reversed()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Recent Progress")
                    .font(.headline)
                Spacer()
                Text(scoredRuns.isEmpty ? "No scored sessions yet" : "Latest scored runs")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            if scoredRuns.isEmpty {
                Text("Run a session and stop coaching to generate the first score.")
                    .foregroundStyle(.secondary)
            } else {
                HStack(alignment: .bottom, spacing: 10) {
                    ForEach(scoredRuns) { run in
                        VStack(spacing: 6) {
                            Text("\(run.score ?? 0)")
                                .font(.caption.weight(.bold))
                            RoundedRectangle(cornerRadius: 5)
                                .fill(scoreColor(run.score ?? 0))
                                .frame(width: 34, height: CGFloat(max(18, run.score ?? 0)) * 1.25)
                            Text(run.level.prefix(1))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        .help("\(run.name) · \(run.track) · \(run.level)")
                    }
                    Spacer()
                }
                .frame(height: 160, alignment: .bottom)
            }
        }
        .appPanel()
    }

    func scoreColor(_ score: Int) -> Color {
        if score >= 82 { return AppTheme.green }
        if score >= 65 { return AppTheme.yellow }
        return AppTheme.red
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            PageHeader(title: "Settings", subtitle: "Local runtime", icon: "gearshape")
            VStack(alignment: .leading, spacing: 12) {
                Text("Project Path")
                    .font(.headline)
                Text(model.projectRoot)
                    .font(.system(.body, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                Text(model.projectDataStatus)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .appPanel()
            VStack(alignment: .leading, spacing: 10) {
                Text("Telemetry Helper")
                    .font(.headline)
                Text("./windows-helper/AccTelemetryForwarderNetFx/run-in-crossover.sh")
                    .font(.system(.body, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            .appPanel()
            VStack(alignment: .leading, spacing: 10) {
                Text("Build")
                    .font(.headline)
                Text("6ZA - tutorial speech gate, presentation voice, and stable folder access")
                    .foregroundStyle(.secondary)
            }
            .appPanel()
            Spacer()
        }
        .padding(24)
    }
}

struct AISettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TutorialPageScrollView(page: .ai) {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: "AI", subtitle: model.openAIEnabled ? "Hybrid coach enabled" : "Local coach active", icon: "brain.head.profile")
                HStack(spacing: 14) {
                    MetricTile(label: "Realtime Voice", value: "Local", icon: "speaker.wave.2.fill", tint: AppTheme.green)
                    MetricTile(label: "AI Layer", value: model.openAIEnabled ? "On" : "Off", icon: "sparkles", tint: model.openAIEnabled ? AppTheme.green : AppTheme.gold)
                    MetricTile(label: "Model", value: model.openAIModel, icon: "cpu", tint: AppTheme.cyan)
                }
            VStack(alignment: .leading, spacing: 14) {
                Toggle("Use OpenAI for Rachel's post-session AI phrasing", isOn: $model.openAIEnabled)
                    .toggleStyle(.switch)
                SecureField("OpenAI API key", text: $model.openAIAPIKey)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 620)
                HStack {
                    Text("Model")
                    TextField("Model", text: $model.openAIModel)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 190)
                    Button("Save AI Settings") {
                        model.saveOpenAISettings()
                    }
                    .buttonStyle(.borderedProminent)
                    Button("Clear Key") {
                        model.clearOpenAIKey()
                    }
                    .buttonStyle(.bordered)
                    Button("Test OpenAI Connection") {
                        model.testOpenAIConnection()
                    }
                    .buttonStyle(.bordered)
                }
                Text(model.openAIStatus)
                    .foregroundStyle(.secondary)
                Text("The key is stored in macOS Keychain and passed only as OPENAI_API_KEY to the local Python coach process.")
                    .foregroundStyle(.secondary)
            }
            .padding()
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            .tutorialTarget(.aiSettings)

            VStack(alignment: .leading, spacing: 10) {
                Text("Current Role")
                    .font(.headline)
                Text("Deterministic telemetry analysis remains authoritative. OpenAI only improves wording, summaries, and next-lap planning from the already generated coach context.")
                    .foregroundStyle(.secondary)
            }
            .appPanel()
            VStack(alignment: .leading, spacing: 10) {
                Text("Planned AI Features")
                    .font(.headline)
                Text("Mistake analyzer, personalized training plan, driver-style adaptation, level-based curriculum, and better post-session explanations. If OpenAI is off or unavailable, local real-time voice coaching continues normally.")
                    .foregroundStyle(.secondary)
            }
            .appPanel()
            }
            .padding(24)
        }
    }
}

struct ProVideoView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TutorialPageScrollView(page: .proVideo) {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: "Pro Video Reference", subtitle: "Extract pro HUD inputs and compare against your driving", icon: "video.badge.waveform")

                HStack(spacing: 14) {
                    MetricTile(label: "Track", value: model.proVideoTrack, icon: "map", tint: AppTheme.gold)
                    MetricTile(label: "Videos", value: "\(model.proVideoPaths.count)", icon: "film.stack", tint: AppTheme.cyan)
                    MetricTile(label: "Source", value: "Local MP4", icon: "externaldrive", tint: AppTheme.green)
                    MetricTile(label: "AI", value: "Optional later", icon: "brain.head.profile", tint: AppTheme.yellow)
                }

                HStack(alignment: .top, spacing: 14) {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Reference Setup")
                            .font(.headline)
                        HStack {
                            Text("Track")
                            TextField("Track", text: $model.proVideoTrack)
                                .textFieldStyle(.roundedBorder)
                                .frame(width: 160)
                            Text("Car")
                            TextField("Optional car model", text: $model.proVideoCar)
                                .textFieldStyle(.roundedBorder)
                        }
                        HStack {
                            Text("Reference Lap")
                            TextField("0:00 or seconds", text: $model.proVideoLapStart)
                                .textFieldStyle(.roundedBorder)
                                .frame(width: 130)
                            Text("to")
                                .foregroundStyle(.secondary)
                            TextField("2:23 or seconds", text: $model.proVideoLapEnd)
                                .textFieldStyle(.roundedBorder)
                                .frame(width: 130)
                            Text("Leave blank to use the full video.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        TextField("Notes, driver, video source, or session context", text: $model.proVideoNotes)
                            .textFieldStyle(.roundedBorder)
                        HStack {
                            Button { model.chooseProVideos() } label: {
                                Label("Choose MP4", systemImage: "plus.rectangle.on.folder")
                            }
                            .buttonStyle(ActionButtonStyle(prominent: true))
                            Button { model.analyzeProVideos() } label: {
                                Label("Analyze Reference", systemImage: "waveform.path.ecg")
                            }
                            .buttonStyle(ActionButtonStyle(prominent: true))
                            .disabled(model.proVideoPaths.isEmpty)
                            Button { model.openLatestProVideoReport() } label: {
                                Label("Open Latest", systemImage: "doc.text.magnifyingglass")
                            }
                            .buttonStyle(ActionButtonStyle())
                        }
                        Text(model.proVideoStatus)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                        Text(model.proVideoDataStatus)
                            .font(.callout.weight(.semibold))
                            .foregroundStyle(model.proVideoDataStatus.lowercased().contains("succeeded") ? AppTheme.green : AppTheme.yellow)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .appPanel()
                    .tutorialTarget(.proVideoSetup)

                    VStack(alignment: .leading, spacing: 12) {
                        Text("How Rachel Uses It")
                            .font(.headline)
                        ProVideoUseRow(title: "Inputs", text: "Estimate steering, throttle, brake, per-turn time nodes, and best-effort wheel-display speed/gear from the reference lap.")
                        ProVideoUseRow(title: "Comparison", text: "When enabled in Drive, Rachel chooses the turn with the largest pro-reference time loss first.")
                        ProVideoUseRow(title: "Confidence", text: "Speed and gear are read from the steering-wheel display only when reliability checks pass. Rachel otherwise uses pro timing/input traces and live ACC telemetry.")
                    }
                    .frame(width: 360)
                    .appPanel()
                }

                VStack(alignment: .leading, spacing: 10) {
                    Text("Selected Videos")
                        .font(.headline)
                    if model.proVideoPaths.isEmpty {
                        Text("No videos selected yet.")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(model.proVideoPaths, id: \.self) { path in
                            HStack {
                                Image(systemName: "film")
                                    .foregroundStyle(AppTheme.cyan)
                                Text(path)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                    .textSelection(.enabled)
                                Spacer()
                            }
                            .padding(10)
                            .background(AppTheme.panelSoft.opacity(0.48), in: RoundedRectangle(cornerRadius: 8))
                        }
                    }
                }
                .appPanel()

                VStack(alignment: .leading, spacing: 10) {
                    Text("Visual Reference Sheets")
                        .font(.headline)
                    if model.proVideoContactSheets.isEmpty {
                        Text("No contact sheets loaded yet. Choose MP4 files, analyze them, then the extracted visual reference appears here.")
                            .foregroundStyle(.secondary)
                    } else {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 320), spacing: 12)], spacing: 12) {
                            ForEach(model.proVideoContactSheets, id: \.self) { path in
                                VStack(alignment: .leading, spacing: 8) {
                                    if let image = NSImage(contentsOfFile: path) {
                                        Image(nsImage: image)
                                            .resizable()
                                            .scaledToFit()
                                            .clipShape(RoundedRectangle(cornerRadius: 8))
                                    }
                                    Text(path)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                        .textSelection(.enabled)
                                }
                                .padding(10)
                                .background(AppTheme.panelSoft.opacity(0.52), in: RoundedRectangle(cornerRadius: 10))
                            }
                        }
                    }
                }
                .appPanel()

                VStack(alignment: .leading, spacing: 10) {
                    Text("Spa Extraction Checklist")
                        .font(.headline)
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 10)], spacing: 10) {
                        ProVideoChecklistCard(title: "T1 La Source", text: "brake marker, rotation point, traction on exit")
                        ProVideoChecklistCard(title: "T2-T4 Eau Rouge/Raidillon", text: "commitment, compression, track-limit margin")
                        ProVideoChecklistCard(title: "T5-T7 Les Combes/Malmedy", text: "brake board, direction change, exit line")
                        ProVideoChecklistCard(title: "T8-T9 Bruxelles", text: "long release, patience, throttle pickup")
                        ProVideoChecklistCard(title: "T12-T13 Pouhon/Fagnes", text: "minimum speed, steering load, confidence")
                        ProVideoChecklistCard(title: "T18-T19 Bus Stop", text: "straight brake, rotate, traction exit")
                    }
                }
                .appPanel()

                VStack(alignment: .leading, spacing: 10) {
                    Text("Pro Video Report")
                        .font(.headline)
                    ScrollView {
                        Text(model.proVideoReportText)
                            .font(.system(.body, design: .monospaced))
                            .foregroundStyle(Color.white.opacity(0.92))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                    .frame(minHeight: 320)
                    .padding()
                    .background(Color.black.opacity(0.42), in: RoundedRectangle(cornerRadius: 8))
                }
                .appPanel()
            }
            .padding(24)
        }
    }
}

struct ProVideoUseRow: View {
    let title: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "checkmark.seal.fill")
                .foregroundStyle(AppTheme.green)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.callout.weight(.semibold))
                Text(text)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct ProVideoChecklistCard: View {
    let title: String
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.callout.weight(.semibold))
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(AppTheme.panelSoft.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct SetupView: View {
    var body: some View {
        TutorialPageScrollView(page: .setup) {
            VStack(alignment: .leading, spacing: 18) {
            PageHeader(title: "Setup", subtitle: "Reserved for setup analysis", icon: "slider.horizontal.3")
            HStack(spacing: 14) {
                MetricTile(label: "Status", value: "Parked", icon: "lock.fill", tint: AppTheme.gold)
                MetricTile(label: "Scope", value: "UI Only", icon: "wrench.adjustable", tint: AppTheme.cyan)
                MetricTile(label: "Track", value: "Spa", icon: "map", tint: AppTheme.green)
            }
            VStack(alignment: .leading, spacing: 12) {
                Text("Setup Coach")
                    .font(.headline)
                Text("Suspension, aero, tyre pressure, and electronics analysis will stay disabled until the app can separate driving-input problems from setup problems.")
                    .foregroundStyle(.secondary)
            }
            .appPanel()
            .tutorialTarget(.setupCoach)
            Spacer()
            }
            .padding(24)
        }
    }
}

struct ReportButtons: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        HStack {
            Button("Replay") { model.openReport("coach_replay.md") }.buttonStyle(ActionButtonStyle())
            Button("Decision") { model.openReport("coach_decision_report.md") }.buttonStyle(ActionButtonStyle())
            Button("Profile") { model.openReport("driver_profile_update.md") }.buttonStyle(ActionButtonStyle())
            Button("Turn Timing") { model.openReport("turn_timing.md") }.buttonStyle(ActionButtonStyle())
            Button("Reference") { model.openReport("reference_comparison.md") }.buttonStyle(ActionButtonStyle())
            Button("Session") { model.openReport("session_summary.md") }.buttonStyle(ActionButtonStyle())
            Button("AI Output") { model.openReport("ai_coach_output.md") }.buttonStyle(ActionButtonStyle())
        }
    }
}

struct ReportText: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ScrollView {
            Text(model.reportText)
                .font(.system(.body, design: .monospaced))
                .foregroundStyle(Color.white.opacity(0.9))
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
        }
        .padding()
        .background(Color.black.opacity(0.42), in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(AppTheme.border, lineWidth: 1)
        )
    }
}
