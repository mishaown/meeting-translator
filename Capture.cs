using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using NAudio.CoreAudioApi;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;

// Runs in a separate process so model inference never blocks recording.
public static class MeetingCapture
{
    private sealed class Track : IDisposable
    {
        public WasapiCapture Capture;
        public WaveFileWriter Writer;
        public ManualResetEvent Stopped = new ManualResetEvent(false);
        public Exception Error;
        public volatile bool Paused;
        private long frames;
        private readonly Stopwatch clock;
        private readonly object gate = new object();

        public Track(WasapiCapture capture, string path, Stopwatch sharedClock)
        {
            clock = sharedClock;
            Capture = capture;
            Capture.WaveFormat = new WaveFormat(16000, 16, 1);
            var stream = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.ReadWrite);
            Writer = new WaveFileWriter(stream, Capture.WaveFormat);
            Writer.Flush();
            Capture.DataAvailable += (s, e) => {
                try {
                    lock (gate) {
                        if (Paused) return;
                        // Preserve elapsed time across startup offsets or missing packets.
                        long packetFrames = e.BytesRecorded / 2;
                        long expected = (long)(clock.Elapsed.TotalSeconds * 16000) - packetFrames;
                        long missing = expected - frames;
                        if ((frames == 0 && missing > 0) || missing > 3200) {
                            byte[] silence = new byte[(int)Math.Min(missing, 16000) * 2];
                            while (missing > 0) {
                                int count = (int)Math.Min(missing, silence.Length / 2);
                                Writer.Write(silence, 0, count * 2);
                                frames += count;
                                missing -= count;
                            }
                        }
                        Writer.Write(e.Buffer, 0, e.BytesRecorded);
                        frames += packetFrames;
                        Writer.Flush();
                    }
                } catch (Exception ex) { Error = ex; }
            };
            Capture.RecordingStopped += (s, e) => { if (e.Exception != null) Error = e.Exception; Stopped.Set(); };
        }

        public void Dispose()
        {
            Capture.StopRecording();
            if (!Stopped.WaitOne(5000)) throw new TimeoutException("Audio capture did not stop");
            Capture.Dispose();
            lock (gate) { Writer.Dispose(); }
            Stopped.Dispose();
        }
    }

    public static string Devices()
    {
        using (var devices = new MMDeviceEnumerator()) {
            var lines = new List<string>();
            foreach (DataFlow flow in new[] { DataFlow.Capture, DataFlow.Render }) {
                var dev = devices.GetDefaultAudioEndpoint(flow, Role.Multimedia);
                lines.Add(flow + ": " + dev.FriendlyName);
            }
            return String.Join(Environment.NewLine, lines);
        }
    }

    public static void Run(string directory, string source, double seconds, int parentId)
    {
        Directory.CreateDirectory(directory);
        var tracks = new List<Track>();
        WasapiOut silentPlayer = null;
        var clock = new Stopwatch();
        Process parent = parentId > 0 ? Process.GetProcessById(parentId) : null;
        try {
            if (source == "both" || source == "system") {
                // WASAPI loopback stops delivering packets when no app renders audio.
                // A zero-valued stream keeps the timeline moving without audible output.
                silentPlayer = new WasapiOut(AudioClientShareMode.Shared, false, 100);
                silentPlayer.Init(new SilenceProvider(WaveFormat.CreateIeeeFloatWaveFormat(48000, 2)));
                silentPlayer.Play();
                tracks.Add(new Track(new WasapiLoopbackCapture(), Path.Combine(directory, "system.wav"), clock));
            }
            if (source == "both" || source == "mic") {
                tracks.Add(new Track(new WasapiCapture(), Path.Combine(directory, "microphone.wav"), clock));
            }
            clock.Start();
            foreach (var track in tracks) track.Capture.StartRecording();
            File.WriteAllText(Path.Combine(directory, "capture.ready"), DateTime.UtcNow.ToString("o"));
            File.WriteAllText(Path.Combine(directory, "capture.state"), "recording");
            bool paused = false;
            while (!File.Exists(Path.Combine(directory, "capture.stop")) && (seconds <= 0 || clock.Elapsed.TotalSeconds < seconds)
                   && (parent == null || !parent.HasExited)) {
                bool requested = File.Exists(Path.Combine(directory, "capture.pause"));
                if (requested != paused) {
                    if (requested) {
                        foreach (var track in tracks) track.Paused = true;
                        clock.Stop();
                    } else {
                        clock.Start();
                        foreach (var track in tracks) track.Paused = false;
                    }
                    paused = requested;
                    File.WriteAllText(Path.Combine(directory, "capture.state"), paused ? "paused" : "recording");
                }
                foreach (var track in tracks) if (track.Error != null) throw track.Error;
                Thread.Sleep(50);
            }
        } finally {
            Exception failure = null;
            foreach (var track in tracks) {
                try { track.Dispose(); } catch (Exception ex) { failure = ex; }
            }
            if (silentPlayer != null) { silentPlayer.Stop(); silentPlayer.Dispose(); }
            if (parent != null) parent.Dispose();
            File.WriteAllText(Path.Combine(directory, "capture.finished"), clock.Elapsed.TotalSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture));
            if (failure != null) throw failure;
        }
    }
}
