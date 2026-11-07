#include "solution.h"
#include <fstream>
#include <sstream>
#include <iomanip>
#include <iostream>
int main(int argc,char **argv){
 if(argc!=4){std::cerr<<"usage: route_probe native.txt routes.txt output.json\n";return 2;}
 ArgumentParser parser;parser.addArgument("--problem",1,false);parser.addArgument("--benchmark_reference");
 char arg0[]="probe",arg1[]="--problem",arg3[]="--benchmark_reference";char *args[]={arg0,arg1,argv[1],arg3};
 parser.parse(4,args);Data d(parser);
 std::ifstream in(argv[2]);std::ofstream out(argv[3]);std::string line;
 bool first=true,feasible=true;double total=0;int vehicles=0;std::vector<int> counts(d.customer_num+1,0);
 out<<std::setprecision(17)<<"{\"routes\":[";
 while(std::getline(in,line)){
  std::istringstream row(line);int node;std::vector<int> route;while(row>>node)route.push_back(node);
  if(route.empty())continue;bool serves=false;
  for(int n:route)if(n>0&&n<=d.customer_num){++counts[n];serves=true;}
  if(!serves || route.size()<3) { feasible=false; continue; }
  std::vector<status> states;int flag=0,negative=-1;double cost=0;
  reference_route_status(route,states,d,flag,cost,negative,false);
  if(!first)out<<",";first=false;out<<"{\"flag\":"<<flag<<",\"cost\":";
  if(std::isfinite(cost))out<<cost;else out<<"null";
  out<<",\"negative_index\":"<<negative<<",\"states\":[";
  if(flag==1)for(size_t i=0;i<states.size();++i){if(i)out<<",";auto &s=states[i];out<<"{\"arrival_s\":"<<s.arr_time<<",\"departure_s\":"<<s.dep_time<<",\"arrival_kwh\":"<<s.arr_RD*d.vehicle.consumption_rate<<",\"departure_kwh\":"<<s.dep_RD*d.vehicle.consumption_rate<<"}";}
  out<<"]}";if(flag==1){total+=cost;++vehicles;}else feasible=false;
 }
 bool coverage=true;for(int i=1;i<=d.customer_num;++i)if(counts[i]!=1)coverage=false;
 feasible=feasible&&coverage;
 out<<"],\"complete_and_feasible\":"<<(feasible?"true":"false")<<",\"coverage_exactly_once\":"<<(coverage?"true":"false")<<",\"cost\":";
 if(feasible)out<<total;else out<<"null";out<<",\"vehicle_count\":"<<vehicles<<"}\n";
}
