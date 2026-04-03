import QtQuick
import QtQuick.Controls
import QtQuick.Controls.FluentWinUI3

Window {
    width: 400
    height: 300
    visible: false
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
    title: "Agent Desktop Preview"

    FluentWinUI3.theme: FluentWinUI3.Dark
    FluentWinUI3.accent: "#38bdf8"

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

    Column {
        anchors.centerIn: parent
        spacing: 8

        Label {
            text: "Agent Desktop Preview"
            color: "#f8fafc"
            font.pixelSize: 16
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
        }

        Label {
            width: 250
            wrapMode: Text.Wrap
            text: uiState.sidecarVisible ? "The preview surface is active." : "Waiting for agent workspace output."
            color: "#93a7bf"
            horizontalAlignment: Text.AlignHCenter
        }
    }
}
