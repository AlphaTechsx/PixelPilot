import QtQuick
import QtQuick.Controls
import QtQuick.Controls.FluentWinUI3
import QtQuick.Layouts

ApplicationWindow {
    id: root
    width: 920
    height: uiState.expanded ? 660 : 84
    minimumWidth: 920
    maximumWidth: 920
    minimumHeight: 84
    maximumHeight: uiState.expanded ? 660 : 84
    visible: false
    color: "transparent"
    title: "Pixel Pilot"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool

    FluentWinUI3.theme: FluentWinUI3.Dark
    FluentWinUI3.accent: "#38bdf8"

    Rectangle {
        anchors.fill: parent
        radius: 24
        color: "#0f172a"
        border.width: 1
        border.color: "#22324a"

        gradient: Gradient {
            GradientStop { position: 0.0; color: "#142033" }
            GradientStop { position: 1.0; color: "#0a1220" }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 50
            radius: 18
            color: "#172537"
            border.width: 1
            border.color: "#253a52"

            RowLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 8

                MouseArea {
                    Layout.fillHeight: true
                    Layout.preferredWidth: 150
                    onPressed: root.startSystemMove()

                    RowLayout {
                        anchors.fill: parent
                        spacing: 10

                        Rectangle {
                            width: 28
                            height: 28
                            radius: 14
                            color: "#0ea5e9"

                            Text {
                                anchors.centerIn: parent
                                text: "P"
                                color: "white"
                                font.pixelSize: 15
                                font.bold: true
                            }
                        }

                        ColumnLayout {
                            spacing: 0

                            Label {
                                text: "Pixel Pilot"
                                color: "#f8fafc"
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                            }

                            Label {
                                text: uiState.liveSessionState === "disconnected" ? "Desktop Agent" : uiState.liveSessionState.toUpperCase()
                                color: "#93a7bf"
                                font.pixelSize: 10
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: 14
                    color: "#0b1422"
                    border.width: 1
                    border.color: "#1e3148"

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 6
                        spacing: 8

                        TextField {
                            id: inputField
                            Layout.fillWidth: true
                            selectByMouse: true
                            placeholderText: !uiState.liveAvailable
                                             ? (uiState.liveUnavailableReason || "Gemini Live unavailable")
                                             : uiState.liveEnabled
                                               ? (uiState.liveVoiceActive ? "Type or speak while mic is active..." : "Type or speak to Gemini Live...")
                                               : "Turn AI on to chat with Pixie..."
                            enabled: uiState.liveAvailable && uiState.liveEnabled
                            color: "#f8fafc"
                            placeholderTextColor: "#72879f"
                            background: null
                            onAccepted: {
                                uiActions.submitCommand(text)
                                clear()
                                forceActiveFocus()
                            }
                        }

                        ToolButton {
                            text: uiState.liveVoiceActive ? "Mic On" : "Mic"
                            enabled: uiState.liveAvailable && uiState.liveEnabled
                            onClicked: {
                                uiActions.requestLiveVoice(!uiState.liveVoiceActive)
                                inputField.forceActiveFocus()
                            }
                        }

                        Button {
                            text: "Go"
                            enabled: uiState.liveAvailable && uiState.liveEnabled
                            onClicked: {
                                uiActions.submitCommand(inputField.text)
                                inputField.clear()
                                inputField.forceActiveFocus()
                            }
                        }
                    }
                }

                Button {
                    text: uiState.liveEnabled ? "AI On" : "AI"
                    enabled: uiState.liveAvailable
                    highlighted: uiState.liveEnabled
                    onClicked: {
                        uiActions.requestLiveMode(!uiState.liveEnabled)
                        if (!uiState.liveEnabled) {
                            inputField.forceActiveFocus()
                        }
                    }
                }

                ComboBox {
                    id: modeCombo
                    model: ["GUIDANCE", "SAFE", "AUTO"]
                    currentIndex: Math.max(0, model.indexOf(uiState.operationMode))
                    onActivated: uiActions.selectMode(currentText)
                }

                ComboBox {
                    id: visionCombo
                    model: ["ROBO", "OCR"]
                    currentIndex: Math.max(0, model.indexOf(uiState.visionMode))
                    onActivated: uiActions.selectVision(currentText)
                }

                Button {
                    text: uiState.workspace === "agent" ? (uiState.agentViewVisible ? "AG+" : "AG") : "USR"
                    highlighted: uiState.workspace === "agent"
                    enabled: uiState.workspace === "agent"
                    onClicked: uiActions.toggleAgentView()
                }

                ToolButton {
                    text: uiState.expanded ? "Hide" : "More"
                    onClicked: uiActions.toggleExpanded()
                }

                ToolButton {
                    text: "_"
                    onClicked: uiActions.requestMinimize()
                }

                ToolButton {
                    text: "X"
                    onClicked: uiActions.requestQuit()
                }
            }
        }

        Loader {
            Layout.fillWidth: true
            Layout.fillHeight: true
            active: uiState.expanded
            asynchronous: true

            sourceComponent: RowLayout {
                spacing: 12

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: 20
                    color: "#111b2b"
                    border.width: 1
                    border.color: "#203247"

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10

                        RowLayout {
                            Layout.fillWidth: true

                            Label {
                                text: "Live Transcript"
                                color: "#f8fafc"
                                font.pixelSize: 16
                                font.weight: Font.DemiBold
                            }

                            Item { Layout.fillWidth: true }

                            Label {
                                text: uiState.liveVoiceActive ? "Voice active" : "Text mode"
                                color: "#7dd3fc"
                                font.pixelSize: 12
                            }
                        }

                        ListView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            spacing: 8
                            model: messageFeed

                            delegate: Rectangle {
                                required property string kind
                                required property string text
                                required property string speaker
                                required property bool isFinal

                                width: ListView.view.width
                                height: bubble.implicitHeight + 18
                                radius: 16
                                color: kind === "error" ? "#3b1721"
                                      : kind === "user" ? "#0d2c52"
                                      : kind === "assistant" ? "#10263d"
                                      : kind === "activity" ? "#172c22"
                                      : "#162235"
                                border.width: 1
                                border.color: kind === "error" ? "#7f1d1d"
                                              : kind === "activity" ? "#14532d"
                                              : "#24384d"

                                Text {
                                    id: bubble
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    text: parent.text
                                    wrapMode: Text.Wrap
                                    color: "#f8fafc"
                                    font.pixelSize: 13
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 300
                    Layout.fillHeight: true
                    radius: 20
                    color: "#111b2b"
                    border.width: 1
                    border.color: "#203247"

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10

                        Label {
                            text: "Agent Desktop"
                            color: "#f8fafc"
                            font.pixelSize: 16
                            font.weight: Font.DemiBold
                        }

                        Loader {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            active: uiState.agentPreviewAvailable
                            asynchronous: true

                            sourceComponent: Rectangle {
                                radius: 16
                                color: "#0b1422"
                                border.width: 1
                                border.color: "#24425d"

                                Column {
                                    anchors.centerIn: parent
                                    spacing: 8

                                    Label {
                                        text: uiState.sidecarVisible ? "Preview surface active" : "Preview ready"
                                        color: "#f8fafc"
                                        horizontalAlignment: Text.AlignHCenter
                                    }

                                    Label {
                                        text: "The Python runtime owns capture and safety policy."
                                        width: 220
                                        wrapMode: Text.Wrap
                                        color: "#93a7bf"
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                }
                            }
                        }

                        Loader {
                            Layout.fillWidth: true
                            active: !uiState.agentPreviewAvailable

                            sourceComponent: Rectangle {
                                height: 80
                                radius: 16
                                color: "#0b1422"
                                border.width: 1
                                border.color: "#203247"

                                Label {
                                    anchors.centerIn: parent
                                    text: "Agent preview unavailable"
                                    color: "#93a7bf"
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
