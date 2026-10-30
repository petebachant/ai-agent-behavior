% towtank_proc.m  - J. Rivera, 2024
% mean drag from one run file. usage: F = towtank_proc('run_001.csv', 0.02)
function F = towtank_proc(fname, sens)
    d = readmatrix(fname, 'NumHeaderLines', 3);
    t = d(:,1); v = d(:,2);
    tare = mean(v(t < 1));
    f = (v - tare) / sens;
    F = mean(f(t > 3 & t < 8));
end
