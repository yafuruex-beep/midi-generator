import Foundation
import AVFoundation
import AudioToolbox
let args=CommandLine.arguments
guard args.count==5,let seconds=Double(args[3]),["guitar","drums","band"].contains(args[4]) else {fatalError("renderer input.mid output.wav seconds guitar|drums|band")}
let engine=AVAudioEngine()
let guitar=AVAudioUnitSampler(), drums=AVAudioUnitSampler()
engine.attach(guitar);engine.attach(drums)
let format=AVAudioFormat(standardFormatWithSampleRate:44100,channels:2)!
engine.connect(guitar,to:engine.mainMixerNode,format:format)
engine.connect(drums,to:engine.mainMixerNode,format:format)
let bank=URL(fileURLWithPath:"/System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls")
try guitar.loadSoundBankInstrument(at:bank,program:30,bankMSB:UInt8(kAUSampler_DefaultMelodicBankMSB),bankLSB:0)
try drums.loadSoundBankInstrument(at:bank,program:0,bankMSB:UInt8(kAUSampler_DefaultPercussionBankMSB),bankLSB:0)
// Same engine, same sequencer, same clock, same stem gains. No timing offset.
engine.mainMixerNode.outputVolume=0.5
let sequencer=AVAudioSequencer(audioEngine:engine)
try sequencer.load(from:URL(fileURLWithPath:args[1]),options:[])
if args[4]=="band" && sequencer.tracks.count != 2 {fatalError("Band must contain guitar then drums")}
for (i,track) in sequencer.tracks.enumerated() {track.destinationAudioUnit = args[4]=="drums" || (args[4]=="band" && i==1) ? drums : guitar}
try engine.enableManualRenderingMode(.offline,format:format,maximumFrameCount:4096)
try engine.start()
var settings=format.settings;settings.removeValue(forKey:AVLinearPCMIsNonInterleaved)
var output:AVAudioFile?=try AVAudioFile(forWriting:URL(fileURLWithPath:args[2]),settings:settings)
let buffer=AVAudioPCMBuffer(pcmFormat:format,frameCapacity:4096)!
sequencer.currentPositionInBeats=0;try sequencer.start()
let target=AVAudioFramePosition((seconds*44100).rounded());var retries=0
while engine.manualRenderingSampleTime<target {
 let frames=AVAudioFrameCount(min(4096,target-engine.manualRenderingSampleTime))
 let status=try engine.renderOffline(frames,to:buffer)
 switch status {
 case .success:try output!.write(from:buffer);retries=0
 case .cannotDoInCurrentContext,.insufficientDataFromInputNode:retries+=1;if retries>100 {fatalError("Offline engine stalled")}
 default:fatalError("Offline rendering error")
 }
}
sequencer.stop();engine.stop();output=nil
print("System DLS, melodic GM31 + percussion bank GM kit; common sequencer; mode \(args[4])")
