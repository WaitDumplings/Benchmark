#pragma once
// Included after HMA's status declaration. The public benchmark verifier remains external.
// Units: distance km; energy kWh; time seconds; capacity and delivery in the same volume unit.
inline void reference_route_status(std::vector<int> &nl, std::vector<status> &sl,
                                  Data &data, int &flag, double &cost,
                                  int &negative_index, bool adjust_stations)
{
    reference_check_deadline();
    const double eps = 1e-5; // canonical validate_routes tolerance
    flag = 0; cost = double(INFINITY); negative_index = -1;
    if (nl.size() < 2 || nl.front() != data.DC || nl.back() != data.DC) return;
    for (size_t i=1;i+1<nl.size();++i) if (nl[i]==data.DC) return;
    sl.resize(nl.size());
    double load = 0.0, distance = 0.0, time = data.start_time;
    int customers = 0;
    for (int n : nl) {
        if (n < 0 || n >= data.node_num) return;
        load += data.node[n].delivery;
        customers += data.node[n].type == 1;
    }
    if (load > data.vehicle.capacity + eps) { flag = 2; return; }
    sl[0] = {time, time, data.max_distance_reachable, data.max_distance_reachable};
    for (size_t i=1; i<nl.size(); ++i) {
        int previous = nl[i-1], node = nl[i];
        double remaining = sl[i-1].dep_RD - data.energy_distance(previous,node);
        // Preserve the author's bounded station replacement rule in its evolution path.
        if (adjust_stations && data.node[node].type == 2 && i+1 < nl.size()) {
            auto segment_distance = [&](int candidate) {
                double d = data.energy_distance(candidate,nl[i+1]);
                size_t j = i+1;
                while (data.node[nl[j]].type == 1 && j+1 < nl.size()) {
                    d += data.energy_distance(nl[j],nl[j+1]); ++j;
                }
                return d;
            };
            if (remaining < -eps || segment_distance(node) > data.max_distance_reachable + eps) {
                for (int k=1; k<data.station_range; ++k) {
                    int candidate = data.optimal_staion[previous][nl[i+1]][k];
                    double candidate_remaining = sl[i-1].dep_RD - data.energy_distance(previous,candidate);
                    if (candidate_remaining >= -eps && segment_distance(candidate) <= data.max_distance_reachable + eps) {
                        node = candidate; nl[i] = candidate; remaining = candidate_remaining; break;
                    }
                }
            }
        }
        time += data.time[previous][node];
        sl[i].arr_time = time; sl[i].arr_RD = remaining;
        if (data.node[node].type != 2 && std::max(time,data.node[node].start) > data.node[node].end + eps) { flag = 3; return; }
        if (remaining < -eps) { flag = 4; negative_index = int(i); return; }
        if (data.node[node].type != 2)
            time = std::max(time, data.node[node].start) + data.node[node].s_time;
        load -= data.node[node].delivery;
        if (load < -eps || load > data.vehicle.capacity + eps) { flag = 2; return; }
        if (data.node[node].type == 2) {
            // Effective station power is already derated in the exported input, exactly once.
            double added_kwh = std::max(0.0, (data.max_distance_reachable-remaining)*data.vehicle.consumption_rate);
            time += added_kwh * data.charging_seconds_per_kwh.at(node);
            sl[i].dep_RD = data.max_distance_reachable;
        } else sl[i].dep_RD = remaining;
        sl[i].dep_time = time;
        distance += data.dist[previous][node];
    }
    flag = 1;
    cost = (customers ? data.vehicle.d_cost : 0.0) + data.vehicle.unit_cost * distance;
}
