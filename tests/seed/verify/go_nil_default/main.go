package main
import "fmt"
func main() {
	var ch chan int
	select {
	case <-ch:
		fmt.Println("recv")
	default:
		fmt.Println("default")
	}
}
