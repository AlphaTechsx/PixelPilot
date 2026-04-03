import QtQuick
import QtQuick.Controls
import QtQuick.Controls.FluentWinUI3

Window {
    id: root
    width: 404
    height: 44
    visible: false
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput
    title: "Pixel Pilot Hidden"

    FluentWinUI3.theme: FluentWinUI3.Dark
    FluentWinUI3.accent: "#38bdf8"

    function statusText() {
        if (!uiState.liveAvailable) {
            return uiState.liveUnavailableReason || "Gemini Live unavailable"
        }
        if (uiState.liveVoiceActive) {
            return "Listening..."
        }
        if (uiState.liveSessionState === "thinking") {
            return "Thinking..."
        }
        if (uiState.liveSessionState === "acting") {
            return "Working on the task..."
        }
        if (uiState.liveSessionState === "waiting") {
            return "Waiting for the current action..."
        }
        if (uiState.liveSessionState === "connecting") {
            return "Connecting to Gemini Live..."
        }
        return "Pixel Pilot hidden"
    }

    Rectangle {
        anchors.fill: parent
        radius: 18
        color: "#0f172a"
        border.width: 1
        border.color: "#203247"

        gradient: Gradient {
            GradientStop { position: 0.0; color: "#142033" }
            GradientStop { position: 1.0; color: "#0a1220" }
        }
    }

    Row {
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 12

        Rectangle {
            width: 8
            height: 8
            radius: 4
            color: uiState.liveVoiceActive ? "#22c55e"
                   : uiState.liveSessionState === "thinking" ? "#f59e0b"
                   : uiState.liveEnabled ? "#38bdf8" : "#64748b"
            anchors.verticalCenter: parent.verticalCenter
        }

        Label {
            anchors.verticalCenter: parent.verticalCenter
            text: root.statusText()
            color: "#f8fafc"
            font.pixelSize: 13
        }
    }
}
